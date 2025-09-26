import os
import sqlite3
from fastapi.responses import FileResponse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs
from openai import OpenAI
from datetime import datetime, timedelta
import hashlib
import json
import time
import schedule
from dataclasses import dataclass
from typing import List, Dict, Optional, Set
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import re

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables del archivo .env
load_dotenv()

# Acceder a las variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBSITE_URL = os.getenv("WEBSITE_URL")
SCHOOL_NAME = os.getenv("SCHOOL_NAME")

# Validar variables de entorno INMEDIATAMENTE
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY no encontrada en variables de entorno")
    raise ValueError("OPENAI_API_KEY es requerida")
if not WEBSITE_URL:
    logger.error("WEBSITE_URL no encontrada en variables de entorno")
    raise ValueError("WEBSITE_URL es requerida")
if not SCHOOL_NAME:
    logger.warning("SCHOOL_NAME no encontrada en variables de entorno, usando nombre por defecto")

@dataclass
class WebContent:
    url: str
    title: str
    content: str
    last_updated: datetime
    content_hash: str

class DatabaseManager:
    def __init__(self, db_path: str = "school_assistant.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Inicializa las tablas de la base de datos"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Tabla para contenido web
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS web_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabla para conversaciones
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT UNIQUE NOT NULL,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabla para mensajes
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES conversations (chat_id)
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Base de datos inicializada correctamente")
        except Exception as e:
            logger.error(f"Error inicializando base de datos: {e}")
            raise
    
    def save_web_content(self, content: WebContent):
        """Guarda o actualiza contenido web"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO web_content 
            (url, title, content, content_hash, last_updated)
            VALUES (?, ?, ?, ?, ?)
        ''', (content.url, content.title, content.content, 
              content.content_hash, content.last_updated))
        
        conn.commit()
        conn.close()
    
    def get_all_content(self) -> List[WebContent]:
        """Obtiene todo el contenido web"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT url, title, content, content_hash, last_updated FROM web_content ORDER BY last_updated DESC')
        rows = cursor.fetchall()
        
        conn.close()
        
        return [WebContent(
            url=row[0],
            title=row[1], 
            content=row[2],
            content_hash=row[3],
            last_updated=datetime.fromisoformat(row[4])
        ) for row in rows]
    
    def create_conversation(self, chat_id: str, user_id: str = None) -> str:
        """Crea una nueva conversación"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR IGNORE INTO conversations (chat_id, user_id)
            VALUES (?, ?)
        ''', (chat_id, user_id))
        
        conn.commit()
        conn.close()
        return chat_id
    
    def save_message(self, chat_id: str, role: str, content: str):
        """Guarda un mensaje en la conversación"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Actualizar última actividad
        cursor.execute('''
            UPDATE conversations 
            SET last_activity = CURRENT_TIMESTAMP 
            WHERE chat_id = ?
        ''', (chat_id,))
        
        # Guardar mensaje
        cursor.execute('''
            INSERT INTO messages (chat_id, role, content)
            VALUES (?, ?, ?)
        ''', (chat_id, role, content))
        
        conn.commit()
        conn.close()
    
    def get_conversation_history(self, chat_id: str, limit: int = 20) -> List[Dict]:
        """Obtiene el historial de una conversación - CORREGIDO"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT role, content, timestamp 
            FROM messages 
            WHERE chat_id = ? 
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (chat_id, limit))
        
        messages = cursor.fetchall()
        conn.close()
        
        # Devolver en orden cronológico (ya ordenado ASC en la query)
        return [{"role": msg[0], "content": msg[1], "timestamp": msg[2]} 
                for msg in messages]
    
    def clear_old_conversations(self, days_old: int = 30):
        """Limpia conversaciones antiguas"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cutoff_date = datetime.now() - timedelta(days=days_old)
        
        cursor.execute('''
            DELETE FROM messages 
            WHERE chat_id IN (
                SELECT chat_id FROM conversations 
                WHERE last_activity < ?
            )
        ''', (cutoff_date,))
        
        cursor.execute('''
            DELETE FROM conversations 
            WHERE last_activity < ?
        ''', (cutoff_date,))
        
        conn.commit()
        conn.close()

class ImprovedWebScraper:
    """Scraper mejorado para explorar exhaustivamente el sitio web"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.visited_urls = set()
        self.failed_urls = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        # Patrones de URLs que probablemente no contengan contenido útil
        self.skip_patterns = [
            r'\.pdf$', r'\.jpg$', r'\.png$', r'\.gif$', r'\.css$', r'\.js$',
            r'\.zip$', r'\.doc$', r'\.docx$', r'\.xls$', r'\.xlsx$',
            r'/wp-admin/', r'/wp-content/', r'/wp-includes/',
            r'#$',  # Enlaces que solo van a anclas
            r'\?.*utm_', r'\.xml$', r'\.json$'
        ]
    
    def normalize_url(self, url: str) -> str:
        """Normaliza la URL eliminando parámetros innecesarios y fragmentos"""
        try:
            parsed = urlparse(url)
            # Eliminar fragmentos (#)
            normalized = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path, 
                parsed.params, parsed.query, ''
            ))
            # Eliminar trailing slash si no es la raíz
            if normalized.endswith('/') and len(normalized) > len(f"{parsed.scheme}://{parsed.netloc}/"):
                normalized = normalized.rstrip('/')
            return normalized
        except:
            return url
    
    def is_valid_url(self, url: str) -> bool:
        """Verifica si la URL es válida para scrapear de forma más exhaustiva"""
        try:
            parsed = urlparse(url)
            
            # Debe ser del mismo dominio
            if parsed.netloc != self.domain:
                return False
            
            # Normalizar URL
            normalized_url = self.normalize_url(url)
            
            # No visitar URLs ya procesadas o fallidas
            if normalized_url in self.visited_urls or normalized_url in self.failed_urls:
                return False
            
            # Verificar patrones a evitar
            for pattern in self.skip_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return False
            
            return True
        except:
            return False
    
    def extract_text_content(self, soup: BeautifulSoup, url: str) -> str:
        """Extrae el contenido de texto de forma más inteligente"""
        try:
            # Remover elementos no deseados
            for element in soup(["script", "style", "nav", "footer", "header", "noscript", 
                               "iframe", "object", "embed", "form", "button"]):
                element.decompose()
            
            # Intentar encontrar el contenido principal usando varios selectores
            main_content_selectors = [
                'main', 'article', '.content', '#content', '.main-content',
                '.post-content', '.entry-content', '.page-content',
                '.container', '.wrapper', 'section'
            ]
            
            main_content = ""
            
            # Buscar contenido principal
            for selector in main_content_selectors:
                elements = soup.select(selector)
                if elements:
                    # Tomar el elemento con más texto
                    best_element = max(elements, key=lambda x: len(x.get_text(strip=True)))
                    main_content = best_element.get_text(separator='\n', strip=True)
                    if len(main_content.strip()) > 200:  # Contenido sustancial
                        break
            
            # Si no encontramos contenido principal, usar body completo
            if not main_content or len(main_content.strip()) < 200:
                main_content = soup.get_text(separator='\n', strip=True)
            
            # Limpiar y estructurar el contenido
            lines = []
            for line in main_content.split('\n'):
                cleaned_line = line.strip()
                if cleaned_line and len(cleaned_line) > 2:  # Evitar líneas muy cortas
                    lines.append(cleaned_line)
            
            # Eliminar duplicados consecutivos
            final_lines = []
            prev_line = ""
            for line in lines:
                if line != prev_line:
                    final_lines.append(line)
                    prev_line = line
            
            return '\n'.join(final_lines)
            
        except Exception as e:
            logger.error(f"Error extrayendo contenido de {url}: {e}")
            return ""
    
    def extract_all_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extrae TODOS los enlaces internos de manera más exhaustiva"""
        links = set()
        
        try:
            # Enlaces en <a href="">
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(current_url, href)
                if self.is_valid_url(full_url):
                    links.add(self.normalize_url(full_url))
            
            # Enlaces en botones o elementos con onclick
            for element in soup.find_all(['button', 'div', 'span'], onclick=True):
                onclick = element.get('onclick', '')
                # Buscar URLs en JavaScript
                url_matches = re.findall(r'["\']([^"\']*(?:\.html?|\.php|\/[^"\']*)[^"\']*)["\']', onclick)
                for match in url_matches:
                    full_url = urljoin(current_url, match)
                    if self.is_valid_url(full_url):
                        links.add(self.normalize_url(full_url))
            
            # Enlaces en data attributes
            for element in soup.find_all(attrs={'data-url': True}):
                data_url = element.get('data-url')
                full_url = urljoin(current_url, data_url)
                if self.is_valid_url(full_url):
                    links.add(self.normalize_url(full_url))
            
            # Enlaces específicos para sitios educativos
            # Buscar patrones comunes como /carrera/, /curso/, /programa/
            education_patterns = [
                r'href=["\']([^"\']*(?:carrera|curso|programa|materia|asignatura)[^"\']*)["\']',
                r'href=["\']([^"\']*(?:profesorado|tecnicatura|especializacion)[^"\']*)["\']',
                r'href=["\']([^"\']*(?:inscripcion|requisito|plan)[^"\']*)["\']'
            ]
            
            page_html = str(soup)
            for pattern in education_patterns:
                matches = re.findall(pattern, page_html, re.IGNORECASE)
                for match in matches:
                    full_url = urljoin(current_url, match)
                    if self.is_valid_url(full_url):
                        links.add(self.normalize_url(full_url))
        
        except Exception as e:
            logger.error(f"Error extrayendo enlaces de {current_url}: {e}")
        
        return list(links)
    
    def scrape_page(self, url: str) -> Optional[WebContent]:
        """Scrapea una página individual con mejor manejo de errores"""
        normalized_url = self.normalize_url(url)
        
        try:
            logger.info(f"Scrapeando: {normalized_url}")
            
            response = self.session.get(normalized_url, timeout=20)
            response.raise_for_status()
            
            # Verificar que sea HTML
            content_type = response.headers.get('content-type', '').lower()
            if 'html' not in content_type:
                logger.warning(f"Saltando {normalized_url} - no es HTML ({content_type})")
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Obtener título
            title = self._extract_title(soup, normalized_url)
            
            # Obtener contenido
            content = self.extract_text_content(soup, normalized_url)
            
            if content and len(content.strip()) > 50:  # Contenido mínimo útil
                content_hash = hashlib.md5(content.encode()).hexdigest()
                
                web_content = WebContent(
                    url=normalized_url,
                    title=title,
                    content=content,
                    last_updated=datetime.now(),
                    content_hash=content_hash
                )
                
                logger.info(f"✓ Contenido extraído: {title[:50]}... ({len(content)} chars)")
                return web_content
            else:
                logger.warning(f"Contenido insuficiente en {normalized_url}")
        
        except requests.exceptions.RequestException as e:
            logger.error(f"Error HTTP scrapeando {normalized_url}: {e}")
            self.failed_urls.add(normalized_url)
        except Exception as e:
            logger.error(f"Error general scrapeando {normalized_url}: {e}")
            self.failed_urls.add(normalized_url)
        
        return None
    
    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        """Extrae el título de múltiples fuentes"""
        # Prioridades de títulos
        title_sources = [
            lambda: soup.find('h1'),
            lambda: soup.find('title'),
            lambda: soup.find('meta', property='og:title'),
            lambda: soup.find('meta', attrs={'name': 'title'}),
        ]
        
        for source_func in title_sources:
            try:
                element = source_func()
                if element:
                    if element.name == 'meta':
                        title = element.get('content', '').strip()
                    else:
                        title = element.get_text().strip()
                    
                    if title and len(title) > 3:
                        return title
            except:
                continue
        
        # Título por defecto basado en URL
        return urlparse(url).path.split('/')[-1] or url
    
    def scrape_website_exhaustive(self, max_pages: int = 100, max_depth: int = 5) -> List[WebContent]:
        """Scraping exhaustivo con exploración en profundidad"""
        content_list = []
        urls_by_depth = {0: [self.base_url]}
        current_depth = 0
        
        logger.info(f"🚀 Iniciando scraping exhaustivo de: {self.base_url}")
        logger.info(f"📊 Límites: {max_pages} páginas máximo, {max_depth} niveles de profundidad")
        
        while current_depth < max_depth and len(content_list) < max_pages:
            if current_depth not in urls_by_depth or not urls_by_depth[current_depth]:
                current_depth += 1
                continue
            
            logger.info(f"📂 Procesando nivel {current_depth} - {len(urls_by_depth[current_depth])} URLs")
            
            current_level_urls = urls_by_depth[current_depth]
            next_level_urls = set()
            
            for url in current_level_urls:
                if len(content_list) >= max_pages:
                    break
                
                normalized_url = self.normalize_url(url)
                
                if normalized_url in self.visited_urls:
                    continue
                
                self.visited_urls.add(normalized_url)
                
                # Scrapear la página
                content = self.scrape_page(normalized_url)
                if content:
                    content_list.append(content)
                
                # Obtener enlaces para el siguiente nivel
                if current_depth < max_depth - 1 and len(content_list) < max_pages:
                    try:
                        response = self.session.get(normalized_url, timeout=15)
                        soup = BeautifulSoup(response.content, 'html.parser')
                        new_links = self.extract_all_links(soup, normalized_url)
                        
                        for link in new_links:
                            if link not in self.visited_urls:
                                next_level_urls.add(link)
                        
                        logger.info(f"🔗 Encontrados {len(new_links)} enlaces en {normalized_url}")
                        
                    except Exception as e:
                        logger.warning(f"Error obteniendo enlaces de {normalized_url}: {e}")
                
                # Pausa respetuosa
                time.sleep(0.5)
            
            # Preparar siguiente nivel
            if next_level_urls and current_depth < max_depth - 1:
                urls_by_depth[current_depth + 1] = list(next_level_urls)[:50]  # Limitar URLs por nivel
            
            current_depth += 1
        
        logger.info(f"✅ Scraping exhaustivo completado:")
        logger.info(f"   📄 {len(content_list)} páginas con contenido útil")
        logger.info(f"   🌐 {len(self.visited_urls)} URLs totales visitadas")
        logger.info(f"   ❌ {len(self.failed_urls)} URLs fallidas")
        logger.info(f"   📊 Promedio de caracteres por página: {sum(len(c.content) for c in content_list) // max(1, len(content_list))}")
        
        return content_list

class SchoolAssistant:
    def __init__(self, openai_api_key: str, website_url: str, school_name: str = ""):
        logger.info("Inicializando SchoolAssistant...")
        self.client = OpenAI(api_key=openai_api_key)
        self.website_url = website_url
        self.school_name = school_name
        self.db_manager = DatabaseManager()
        self.scraper = ImprovedWebScraper(website_url)
        self.system_prompt = self._build_system_prompt()
        self._last_content_update = None
        logger.info("SchoolAssistant inicializado correctamente")
    
    def _build_system_prompt(self) -> str:
        """Construye el prompt del sistema con información del colegio - MEJORADO"""
        try:
            content_list = self.db_manager.get_all_content()
            logger.info(f"Construyendo prompt con {len(content_list)} contenidos actualizados")
            
            # Organizar contenido por categorías y priorizar carreras
            knowledge_sections = []
            career_content = []
            general_content = []
            
            for content in content_list:
                # Priorizar contenido sobre carreras
                if any(keyword in content.url.lower() or keyword in content.title.lower() 
                      for keyword in ['carrera', 'profesorado', 'tecnicatura', 'curso', 'programa']):
                    career_content.append(content)
                else:
                    general_content.append(content)
            
            # Añadir contenido de carreras primero (más importante)
            for content in career_content[:10]:  # Más carreras para mayor cobertura
                section = f"""
### {content.title}
URL: {content.url}
Última actualización: {content.last_updated.strftime('%Y-%m-%d %H:%M')}
{content.content[:2500]}{'...' if len(content.content) > 2500 else ''}
"""
                knowledge_sections.append(section)
            
            # Añadir contenido general
            for content in general_content[:8]:  # Más contenido general
                section = f"""
### {content.title}
Última actualización: {content.last_updated.strftime('%Y-%m-%d %H:%M')}
{content.content[:1500]}{'...' if len(content.content) > 1500 else ''}
"""
                knowledge_sections.append(section)
            
            knowledge_base = "\n".join(knowledge_sections)
            
            # Marcar cuando se actualizó el contenido
            self._last_content_update = datetime.now()
            
        except Exception as e:
            logger.error(f"Error construyendo prompt: {e}")
            knowledge_base = "Información del sitio web en proceso de carga..."
        
        return f"""Sos un asistente virtual del {self.school_name} y tu nombre es Agustín (por San Agustín). Sos argentino, amable y cordial. Tu objetivo es ayudar a las familias, estudiantes y visitantes de la mejor manera posible.

## TU PERSONALIDAD:
- **Argentino auténtico**: Hablás natural, usás "vos", "che", y expresiones típicas argentinas sin exagerar
- **Amable y cercano**: Tratás a todos con calidad, como si fueras un miembro más de la comunidad educativa  
- **Directo y claro**: Respondés exactamente lo que te preguntan, sin dar información de más
- **Preguntón cuando es necesario**: Si necesitás aclarar algo para dar una respuesta precisa, preguntás
- **Experto en carreras**: Conocés perfectamente todas las carreras, profesorados y cursos que ofrece la institución
- **Memoria de conversación**: Recordás lo que hablamos antes en esta misma conversación

## INFORMACIÓN COMPLETA DEL COLEGIO (Actualizada: {self._last_content_update.strftime('%Y-%m-%d %H:%M') if self._last_content_update else 'N/A'}):
{knowledge_base}

## CÓMO RESPONDÉS:
1. **Continuidad**: Recordás lo que hablamos en esta conversación y hacés referencia cuando es relevante
2. **Saludá cordialmente** solo al inicio de cada conversación nueva
3. **Escuchá bien** qué te están preguntando específicamente
4. **Respondé directamente** a la pregunta, sin repetir información ya dada
5. **Para consultas sobre carreras**: Proporcioná información detallada incluyendo duración, modalidad, requisitos
6. **Si no tenés la info exacta**, decilo honestamente y ofrecé alternativas
7. **Preguntá para aclarar** si la consulta no está clara
8. **Usá un lenguaje natural argentino** pero profesional
9. **NO repitas** información que ya diste en mensajes anteriores de esta conversación

## EJEMPLOS DE TU FORMA DE HABLAR:
- Primera interacción: "¡Hola! ¿Cómo andás? Soy Agustín, ¿en qué te puedo ayudar?"
- Continuando conversación: "Dale, contame más sobre eso" o "¿Hay algo más específico que te interese saber?"
- Referencias previas: "Como te mencioné recién sobre el Profesorado en Matemática..." 
- "Mirá, esa información específica no la tengo a mano, pero te puedo conectar con..."
- "¿Me podrías aclarar si te referís a nivel terciario o secundario?"

## INFORMACIÓN DE CONTACTO:
- Dirección: Ruta N°1 y Mendoza
- Teléfonos: (03758) 424899
- Email: info@institutosuperiorsanagustin.com   
- Atención: Lunes a Viernes de 7:30 a 12:30hs y de 16:00 a 21hs

## LO QUE NO HACÉS:
- No tirás parrafadas largas si no te las piden
- No repetís información que ya diste en esta conversación
- No inventás datos que no tenés
- No usás un lenguaje demasiado formal o robótico
- No saludás en cada mensaje si ya saludaste al inicio

Recordá: cada familia que te habla está buscando el mejor lugar para su hijo. Tratá cada consulta con la importancia que se merece y mantené el hilo de la conversación fluido."""
    
    def update_content_exhaustive(self):
        """Actualización exhaustiva del contenido del sitio web - MEJORADA"""
        logger.info("🔄 Iniciando actualización exhaustiva de contenido...")
        
        try:
            # Crear nuevo scraper para evitar URLs en cache
            self.scraper = ImprovedWebScraper(self.website_url)
            
            # Usar el scraper mejorado
            new_content = self.scraper.scrape_website_exhaustive(
                max_pages=80,  # Aumentado para capturar más contenido
                max_depth=5   # 5 niveles de profundidad
            )
            
            existing_content = {c.url: c for c in self.db_manager.get_all_content()}
            
            updated_count = 0
            new_count = 0
            
            for content in new_content:
                if content.url not in existing_content:
                    # Contenido completamente nuevo
                    self.db_manager.save_web_content(content)
                    new_count += 1
                elif existing_content[content.url].content_hash != content.content_hash:
                    # Contenido actualizado
                    self.db_manager.save_web_content(content)
                    updated_count += 1
            
            total_changes = updated_count + new_count
            
            # SIEMPRE regenerar el system_prompt después de actualizar contenido
            if total_changes > 0 or len(new_content) > 0:
                logger.info("🔄 Regenerando system prompt con contenido actualizado...")
                self.system_prompt = self._build_system_prompt()
                
                logger.info(f"✅ Contenido actualizado:")
                logger.info(f"   🆕 {new_count} páginas nuevas")
                logger.info(f"   🔄 {updated_count} páginas modificadas") 
                logger.info(f"   📊 Total de páginas procesadas: {len(new_content)}")
                logger.info(f"   📊 Total de cambios: {total_changes}")
            else:
                logger.info("ℹ️  No hay cambios nuevos en el contenido, pero se verificó toda la información")
                # Aún así regeneramos el prompt para asegurar que esté actualizado
                self.system_prompt = self._build_system_prompt()
            
        except Exception as e:
            logger.error(f"❌ Error en actualización exhaustiva: {e}")
            raise
    
    # Mantener compatibilidad con método anterior
    def update_content(self):
        """Método para compatibilidad - llama al exhaustivo"""
        self.update_content_exhaustive()
    
    def get_response(self, chat_id: str, user_message: str, user_id: str = None) -> str:
        """Genera una respuesta para el usuario usando GPT-4 - MEJORADO PARA HILO DE CONVERSACIÓN"""
        try:
            # Crear conversación si no existe
            self.db_manager.create_conversation(chat_id, user_id)
            
            # Obtener historial COMPLETO de la conversación
            history = self.db_manager.get_conversation_history(chat_id, limit=30)
            
            # Construir mensajes para OpenAI
            messages = [{"role": "system", "content": self.system_prompt}]
            
            # IMPORTANTE: Añadir TODO el historial para mantener contexto
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
            
            # Añadir mensaje actual del usuario
            messages.append({"role": "user", "content": user_message})
            
            # Debug: log del contexto
            logger.info(f"Conversación {chat_id}: {len(history)} mensajes previos + mensaje actual")
            
            # Llamada a OpenAI con parámetros optimizados para conversación fluida
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=600,  # Más tokens para respuestas completas
                temperature=0.1,  # Más consistente pero natural
                presence_penalty=0.3,  # Evita repetición fuerte
                frequency_penalty=0.2   # Promueve variedad
            )
            
            assistant_response = response.choices[0].message.content
            
            # Guardar SOLO el mensaje nuevo (no repetir historial)
            self.db_manager.save_message(chat_id, "user", user_message)
            self.db_manager.save_message(chat_id, "assistant", assistant_response)
            
            logger.info(f"Respuesta generada para {chat_id}: {assistant_response[:100]}...")
            
            return assistant_response
            
        except Exception as e:
            logger.error(f"Error generando respuesta: {e}")
            return "Uy, disculpá, tengo un problemita técnico. ¿Podés intentar de nuevo en un ratito? Si sigue sin andar, mejor llamá directamente al colegio."

    def get_conversation_summary(self, chat_id: str) -> str:
        """Obtiene un resumen de la conversación actual"""
        try:
            history = self.db_manager.get_conversation_history(chat_id, limit=10)
            if not history:
                return "Conversación nueva"
            
            topics = []
            for msg in history:
                if msg["role"] == "user" and len(msg["content"]) > 10:
                    topics.append(msg["content"][:50])
            
            return f"Temas consultados: {', '.join(topics[:3])}"
        except:
            return "Conversación activa"

# API REST para el widget
app = Flask(__name__)
CORS(app)

# ======== INICIALIZACIÓN AUTOMÁTICA ========
assistant = None

def init_assistant():
    """Inicializa el asistente"""
    global assistant
    
    try:
        logger.info("🚀 Inicializando Agustín, tu asistente del colegio...")
        
        school_name = SCHOOL_NAME or "Colegio"
        
        logger.info(f"Configuración:")
        logger.info(f"- URL: {WEBSITE_URL}")
        logger.info(f"- Escuela: {school_name}")
        logger.info(f"- OpenAI API: {'✓ Configurada' if OPENAI_API_KEY else '✗ Falta'}")
        
        assistant = SchoolAssistant(OPENAI_API_KEY, WEBSITE_URL, school_name)
        
        # Realizar scraping exhaustivo inicial
        try:
            logger.info("Realizando scraping exhaustivo inicial...")
            assistant.update_content_exhaustive()
            logger.info("✓ Sistema completamente listo")
        except Exception as e:
            logger.warning(f"Scraping inicial falló, continuando: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Error fatal inicializando asistente: {e}")
        return False

# Intentar inicializar inmediatamente
try:
    success = init_assistant()
    if not success:
        logger.error("Inicialización falló")
except Exception as e:
    logger.error(f"Error en inicialización automática: {e}")

# ======== ENDPOINTS MEJORADOS ========
@app.route('/api/webhook/website', methods=['POST'])
def webhook_chat():
    """Endpoint compatible con el formato del widget - MEJORADO"""
    global assistant
    
    if not assistant:
        logger.error("Assistant no inicializado - intentando reinicializar...")
        if not init_assistant():
            return jsonify({"text": "El asistente no está disponible en este momento. Por favor intenta más tarde."}), 500
    
    try:
        data = request.json
        logger.info(f"Received data: {data}")
        
        message_body = data.get('body', '').strip()
        external_id = data.get('externalId', f"web_{int(time.time())}")
        
        if not message_body:
            return jsonify({"text": "Por favor escribí tu consulta."}), 400
        
        # Generar respuesta manteniendo el hilo de conversación
        response = assistant.get_response(external_id, message_body, external_id)
        
        logger.info(f"Generated response for {external_id}: {response[:100]}...")
        
        return jsonify({
            "text": response,
            "type": "text",
            "conversation_id": external_id,
            "timestamp": datetime.now().isoformat()
        })
    
    except Exception as e:
        logger.error(f"Error en webhook endpoint: {e}")
        return jsonify({"text": "Disculpá, tuve un problemita técnico. Intentá de nuevo en un ratito."}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    """Endpoint alternativo para compatibilidad"""
    return webhook_chat()

@app.route('/api/conversation/<chat_id>/history', methods=['GET'])
def get_conversation_history(chat_id):
    """Endpoint para obtener historial de conversación"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        history = assistant.db_manager.get_conversation_history(chat_id, limit=50)
        summary = assistant.get_conversation_summary(chat_id)
        
        return jsonify({
            "chat_id": chat_id,
            "summary": summary,
            "message_count": len(history),
            "messages": history
        })
    except Exception as e:
        logger.error(f"Error obteniendo historial: {e}")
        return jsonify({"error": "Error retrieving history"}), 500

@app.route('/api/conversation/<chat_id>/clear', methods=['POST'])
def clear_conversation(chat_id):
    """Endpoint para limpiar una conversación específica"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        conn = sqlite3.connect(assistant.db_manager.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM messages WHERE chat_id = ?', (chat_id,))
        cursor.execute('DELETE FROM conversations WHERE chat_id = ?', (chat_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({"message": f"Conversación {chat_id} eliminada"})
    except Exception as e:
        logger.error(f"Error limpiando conversación: {e}")
        return jsonify({"error": "Error clearing conversation"}), 500

@app.route('/api/health', methods=['GET'])
def health():
    """Endpoint de salud - MEJORADO"""
    global assistant
    
    status_info = {
        "status": "ok" if assistant else "error",
        "timestamp": datetime.now().isoformat(),
        "assistant_initialized": assistant is not None,
        "environment": {
            "openai_api_key": "configured" if OPENAI_API_KEY else "missing",
            "website_url": "configured" if WEBSITE_URL else "missing",
            "school_name": "configured" if SCHOOL_NAME else "using_default"
        }
    }
    
    if assistant:
        try:
            content_list = assistant.db_manager.get_all_content()
            status_info["content_pages"] = len(content_list)
            status_info["content_last_update"] = assistant._last_content_update.isoformat() if assistant._last_content_update else None
            status_info["scraper_stats"] = {
                "visited_urls": len(assistant.scraper.visited_urls),
                "failed_urls": len(assistant.scraper.failed_urls)
            }
            
            # Estadísticas de conversaciones
            conn = sqlite3.connect(assistant.db_manager.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM conversations')
            total_conversations = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM messages')
            total_messages = cursor.fetchone()[0]
            conn.close()
            
            status_info["conversation_stats"] = {
                "total_conversations": total_conversations,
                "total_messages": total_messages
            }
            
        except Exception as e:
            status_info["content_pages"] = "error"
            status_info["error"] = str(e)
    
    return jsonify(status_info)

@app.route('/api/update-content', methods=['POST'])
def update_content():
    """Endpoint para actualizar contenido manualmente (ahora exhaustivo) - MEJORADO"""
    global assistant
    
    if not assistant:
        if not init_assistant():
            return jsonify({"error": "Asistente no inicializado"}), 500
    
    try:
        logger.info("🔄 Actualización manual de contenido solicitada")
        
        # Forzar actualización exhaustiva
        assistant.update_content_exhaustive()
        
        # Obtener estadísticas actualizadas
        content_list = assistant.db_manager.get_all_content()
        career_pages = [c for c in content_list if any(keyword in c.url.lower() or keyword in c.title.lower() 
                      for keyword in ['carrera', 'profesorado', 'tecnicatura', 'curso', 'programa'])]
        
        return jsonify({
            "message": "Contenido actualizado exhaustivamente",
            "timestamp": datetime.now().isoformat(),
            "stats": {
                "total_pages": len(content_list),
                "career_pages": len(career_pages),
                "last_update": assistant._last_content_update.isoformat() if assistant._last_content_update else None,
                "visited_urls": len(assistant.scraper.visited_urls),
                "failed_urls": len(assistant.scraper.failed_urls)
            }
        })
    except Exception as e:
        logger.error(f"Error actualizando contenido: {e}")
        return jsonify({"error": f"Error actualizando contenido: {str(e)}"}), 500

@app.route('/api/reinit', methods=['POST'])
def reinit():
    """Endpoint para reinicializar el asistente - MEJORADO"""
    global assistant
    try:
        logger.info("🔄 Reinicialización manual solicitada")
        assistant = None
        success = init_assistant()
        if success:
            return jsonify({
                "message": "Asistente reinicializado correctamente",
                "timestamp": datetime.now().isoformat(),
                "content_pages": len(assistant.db_manager.get_all_content()) if assistant else 0
            })
        else:
            return jsonify({"error": "Error reinicializando asistente"}), 500
    except Exception as e:
        logger.error(f"Error reinicializando: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/conversations', methods=['GET'])
def get_all_conversations():
    """Endpoint para obtener todas las conversaciones"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        conn = sqlite3.connect(assistant.db_manager.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT c.chat_id, c.created_at, c.last_activity,
                   COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON c.chat_id = m.chat_id
            GROUP BY c.chat_id
            ORDER BY c.last_activity DESC
            LIMIT 50
        ''')
        
        conversations = []
        for row in cursor.fetchall():
            conversations.append({
                "chat_id": row[0],
                "created_at": row[1],
                "last_activity": row[2],
                "message_count": row[3]
            })
        
        conn.close()
        
        return jsonify({
            "conversations": conversations,
            "total": len(conversations)
        })
    except Exception as e:
        logger.error(f"Error obteniendo conversaciones: {e}")
        return jsonify({"error": "Error retrieving conversations"}), 500

@app.route("/chat.js")
def serve_chat():
    return send_from_directory("static", "chat.js", mimetype="application/javascript")

@app.route('/')
def home():
    """Página de inicio básica - MEJORADA"""
    global assistant
    
    stats = {}
    if assistant:
        try:
            content_list = assistant.db_manager.get_all_content()
            stats = {
                "content_pages": len(content_list),
                "last_update": assistant._last_content_update.isoformat() if assistant._last_content_update else None,
                "visited_urls": len(assistant.scraper.visited_urls),
                "failed_urls": len(assistant.scraper.failed_urls)
            }
        except:
            stats = {"error": "Error obteniendo estadísticas"}
    
    return jsonify({
        "message": "Agustín - Asistente del Colegio (Versión Mejorada con Hilo de Conversación)",
        "status": "running",
        "features": [
            "Scraping exhaustivo mejorado", 
            "Exploración en profundidad", 
            "Detección inteligente de carreras",
            "Hilo de conversación fluida",
            "Actualización automática de contenido",
            "Memoria de conversación persistente"
        ],
        "endpoints": {
            "chat": "/api/chat",
            "webhook": "/api/webhook/website", 
            "health": "/api/health",
            "update": "/api/update-content",
            "reinit": "/api/reinit",
            "conversations": "/api/conversations",
            "history": "/api/conversation/<chat_id>/history",
            "clear": "/api/conversation/<chat_id>/clear"
        },
        "stats": stats
    })

# ======== TAREAS PROGRAMADAS ========
def scheduled_update():
    """Actualización programada del contenido"""
    global assistant
    if assistant:
        try:
            logger.info("🕐 Ejecutando actualización programada...")
            assistant.update_content_exhaustive()
            # Limpiar conversaciones muy antiguas
            assistant.db_manager.clear_old_conversations(days_old=30)
            logger.info("✅ Actualización programada completada")
        except Exception as e:
            logger.error(f"Error en actualización programada: {e}")

# Programar actualizaciones cada 6 horas
schedule.every(6).hours.do(scheduled_update)

def run_scheduled_tasks():
    """Ejecutar tareas programadas en un hilo separado"""
    while True:
        schedule.run_pending()
        time.sleep(60)  # Verificar cada minuto

# Iniciar tareas programadas en background
import threading
scheduler_thread = threading.Thread(target=run_scheduled_tasks, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    try:
        print("🌐 Servidor listo en http://localhost:5000")
        print("📱 API disponible en /api/chat")
        print("🏥 Health check en /api/health")
        print("🔄 Scraping mejorado y exhaustivo activado")
        print("💬 Sistema de conversación fluida activado")
        print("⏰ Actualizaciones automáticas cada 6 horas")
        
        app.run(host='0.0.0.0', port=5000, debug=False)
        
    except Exception as e:
        logger.error(f"Error fatal al inicializar servidor: {e}")
        print(f"❌ Error al inicializar: {e}")
        print("📋 Verifica que tu archivo .env tenga las variables correctas:")
