import os
import sqlite3
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from openai import OpenAI
from datetime import datetime, timedelta
import hashlib
import json
import time
import tempfile
from dataclasses import dataclass
from typing import List, Dict, Optional, Set
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import re
import threading
import schedule

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Cargar variables del archivo .env
load_dotenv()

# Variables de entorno
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WEBSITE_URL = os.getenv("WEBSITE_URL")
SCHOOL_NAME = os.getenv("SCHOOL_NAME")
OPENAI_ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID")
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID")

# Validar variables críticas
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY no encontrada en variables de entorno")
    raise ValueError("OPENAI_API_KEY es requerida")
if not WEBSITE_URL:
    logger.error("WEBSITE_URL no encontrada en variables de entorno")
    raise ValueError("WEBSITE_URL es requerida")
if not OPENAI_ASSISTANT_ID:
    logger.error("OPENAI_ASSISTANT_ID no encontrada en variables de entorno")
    raise ValueError("OPENAI_ASSISTANT_ID es requerida")
if not OPENAI_VECTOR_STORE_ID:
    logger.error("OPENAI_VECTOR_STORE_ID no encontrada en variables de entorno")
    raise ValueError("OPENAI_VECTOR_STORE_ID es requerida")

# Variables opcionales
if not SCHOOL_NAME:
    logger.warning("SCHOOL_NAME no encontrada, usando nombre por defecto")
    SCHOOL_NAME = "Instituto Superior"

@dataclass
class WebContent:
    url: str
    title: str
    content: str
    last_updated: datetime
    content_hash: str

class DatabaseManager:
    """Manejo de base de datos local para tracking"""
    def __init__(self, db_path: str = "school_assistant.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Inicializa las tablas de la base de datos"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Tabla para tracking de contenido web
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS web_content_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    file_id TEXT,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabla para configuración del asistente
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS assistant_config (
                    id INTEGER PRIMARY KEY,
                    assistant_id TEXT NOT NULL,
                    vector_store_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabla para threads de conversación
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversation_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT UNIQUE NOT NULL,
                    thread_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            logger.info("Base de datos inicializada correctamente")
        except Exception as e:
            logger.error(f"Error inicializando base de datos: {e}")
            raise
    
    def save_content_tracking(self, content: WebContent, file_id: str = None):
        """Guarda tracking de contenido web"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO web_content_tracking 
            (url, title, content_hash, file_id, last_updated, last_scraped)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (content.url, content.title, content.content_hash, file_id, content.last_updated))
        
        conn.commit()
        conn.close()
    
    def get_content_tracking(self) -> List[Dict]:
        """Obtiene tracking de contenido"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT url, title, content_hash, file_id, last_updated, last_scraped 
            FROM web_content_tracking 
            ORDER BY last_updated DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "url": row[0], "title": row[1], "content_hash": row[2],
                "file_id": row[3], "last_updated": row[4], "last_scraped": row[5]
            }
            for row in rows
        ]
    
    def save_thread_mapping(self, external_id: str, thread_id: str):
        """Guarda mapeo de thread para conversaciones"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO conversation_threads 
            (external_id, thread_id, last_activity)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (external_id, thread_id))
        
        conn.commit()
        conn.close()
    
    def get_thread_id(self, external_id: str) -> Optional[str]:
        """Obtiene thread_id para un external_id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT thread_id FROM conversation_threads 
            WHERE external_id = ?
        ''', (external_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        return result[0] if result else None
    
    def save_assistant_config(self, assistant_id: str, vector_store_id: str):
        """Guarda configuración del asistente"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO assistant_config 
            (id, assistant_id, vector_store_id, last_updated)
            VALUES (1, ?, ?, CURRENT_TIMESTAMP)
        ''', (assistant_id, vector_store_id))
        
        conn.commit()
        conn.close()

class ImprovedWebScraper:
    """Scraper optimizado para Vector Store"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.visited_urls = set()
        self.failed_urls = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        self.skip_patterns = [
            r'\.pdf$', r'\.jpg$', r'\.png$', r'\.gif$', r'\.css$', r'\.js$',
            r'\.zip$', r'\.doc$', r'\.docx$', r'\.xls$', r'\.xlsx$',
            r'/wp-admin/', r'/wp-content/', r'/wp-includes/',
            r'#$', r'\?.*utm_', r'\.xml$', r'\.json$'
        ]
    
    def normalize_url(self, url: str) -> str:
        """Normaliza la URL"""
        try:
            parsed = urlparse(url)
            normalized = urlunparse((
                parsed.scheme, parsed.netloc, parsed.path, 
                parsed.params, parsed.query, ''
            ))
            if normalized.endswith('/') and len(normalized) > len(f"{parsed.scheme}://{parsed.netloc}/"):
                normalized = normalized.rstrip('/')
            return normalized
        except:
            return url
    
    def is_valid_url(self, url: str) -> bool:
        """Verifica si la URL es válida para scrapear"""
        try:
            parsed = urlparse(url)
            
            if parsed.netloc != self.domain:
                return False
            
            normalized_url = self.normalize_url(url)
            
            if normalized_url in self.visited_urls or normalized_url in self.failed_urls:
                return False
            
            for pattern in self.skip_patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return False
            
            return True
        except:
            return False
    
    def extract_text_content(self, soup: BeautifulSoup, url: str) -> str:
        """Extrae contenido de texto optimizado para Vector Store"""
        try:
            # Remover elementos no deseados
            for element in soup(["script", "style", "nav", "footer", "header", "noscript", 
                               "iframe", "object", "embed", "form", "button"]):
                element.decompose()
            
            # Intentar encontrar contenido principal
            main_content_selectors = [
                'main', 'article', '.content', '#content', '.main-content',
                '.post-content', '.entry-content', '.page-content',
                '.container', '.wrapper', 'section'
            ]
            
            main_content = ""
            
            for selector in main_content_selectors:
                elements = soup.select(selector)
                if elements:
                    best_element = max(elements, key=lambda x: len(x.get_text(strip=True)))
                    main_content = best_element.get_text(separator='\n', strip=True)
                    if len(main_content.strip()) > 200:
                        break
            
            if not main_content or len(main_content.strip()) < 200:
                main_content = soup.get_text(separator='\n', strip=True)
            
            # Limpiar y estructurar el contenido
            lines = []
            for line in main_content.split('\n'):
                cleaned_line = line.strip()
                if cleaned_line and len(cleaned_line) > 2:
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
        """Extrae todos los enlaces internos"""
        links = set()
        
        try:
            # Enlaces en <a href="">
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(current_url, href)
                if self.is_valid_url(full_url):
                    links.add(self.normalize_url(full_url))
            
            # Enlaces específicos para sitios educativos
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
        """Scrapea una página individual"""
        normalized_url = self.normalize_url(url)
        
        try:
            logger.info(f"Scrapeando: {normalized_url}")
            
            response = self.session.get(normalized_url, timeout=20)
            response.raise_for_status()
            
            content_type = response.headers.get('content-type', '').lower()
            if 'html' not in content_type:
                logger.warning(f"Saltando {normalized_url} - no es HTML")
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Obtener título
            title = self._extract_title(soup, normalized_url)
            
            # Obtener contenido
            content = self.extract_text_content(soup, normalized_url)
            
            if content and len(content.strip()) > 50:
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
        
        except Exception as e:
            logger.error(f"Error scrapeando {normalized_url}: {e}")
            self.failed_urls.add(normalized_url)
        
        return None
    
    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        """Extrae el título de múltiples fuentes"""
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
        
        return urlparse(url).path.split('/')[-1] or url
    
    def scrape_website_exhaustive(self, max_pages: int = 100, max_depth: int = 5) -> List[WebContent]:
        """Scraping exhaustivo optimizado"""
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
                if current_depth < max_depth - 1:
                    try:
                        response = self.session.get(normalized_url, timeout=15)
                        soup = BeautifulSoup(response.content, 'html.parser')
                        new_links = self.extract_all_links(soup, normalized_url)
                        
                        for link in new_links:
                            if link not in self.visited_urls:
                                next_level_urls.add(link)
                        
                    except Exception as e:
                        logger.warning(f"Error obteniendo enlaces de {normalized_url}: {e}")
                
                time.sleep(0.5)  # Pausa respetuosa
            
            # Preparar siguiente nivel
            if next_level_urls and current_depth < max_depth - 1:
                urls_by_depth[current_depth + 1] = list(next_level_urls)[:50]
            
            current_depth += 1
        
        logger.info(f"✅ Scraping completado: {len(content_list)} páginas útiles")
        return content_list

class OpenAIAssistantManager:
    """Maneja OpenAI Assistant + Vector Store"""
    
    def __init__(self, openai_api_key: str, assistant_id: str, vector_store_id: str, school_name: str):
        self.client = OpenAI(api_key=openai_api_key)
        self.assistant_id = assistant_id
        self.vector_store_id = vector_store_id
        self.school_name = school_name
        self.db_manager = DatabaseManager()
        
        # Verificar que el assistant y vector store existen
        self._verify_resources()
    
    def _verify_resources(self):
        """Verifica que el assistant y vector store existen"""
        try:
            # Verificar assistant
            assistant = self.client.beta.assistants.retrieve(self.assistant_id)
            logger.info(f"✓ Assistant encontrado: {assistant.name}")
            
            # Verificar vector store
            vector_store = self.client.beta.vector_stores.retrieve(self.vector_store_id)
            logger.info(f"✓ Vector Store encontrado: {vector_store.name}")
            
            # Guardar configuración
            self.db_manager.save_assistant_config(self.assistant_id, self.vector_store_id)
            
        except Exception as e:
            logger.error(f"Error verificando recursos: {e}")
            raise
    
    def create_document_file(self, content: WebContent) -> str:
        """Crea un archivo de documento para el vector store"""
        try:
            # Crear contenido estructurado para mejor búsqueda
            document_content = f"""Título: {content.title}
URL: {content.url}
Última actualización: {content.last_updated.strftime('%Y-%m-%d %H:%M')}
Institución: {self.school_name}

CONTENIDO:
{content.content}

---
Este documento contiene información oficial de {self.school_name}.
Fuente: {content.url}
Fecha de captura: {content.last_updated.strftime('%Y-%m-%d %H:%M')}
"""
            
            # Crear archivo temporal
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
                temp_file.write(document_content)
                temp_file_path = temp_file.name
            
            # Subir archivo a OpenAI
            with open(temp_file_path, 'rb') as f:
                file_response = self.client.files.create(
                    file=f,
                    purpose='assistants'
                )
            
            # Limpiar archivo temporal
            os.unlink(temp_file_path)
            
            logger.info(f"✓ Archivo creado: {file_response.id} para {content.url}")
            return file_response.id
            
        except Exception as e:
            logger.error(f"Error creando archivo para {content.url}: {e}")
            raise
    
    def update_vector_store_content(self, content_list: List[WebContent]):
        """Actualiza el vector store con nuevo contenido"""
        try:
            logger.info(f"🔄 Actualizando vector store con {len(content_list)} documentos...")
            
            # Obtener archivos actuales en el vector store
            current_files = self.client.beta.vector_stores.files.list(
                vector_store_id=self.vector_store_id
            )
            current_file_ids = [f.id for f in current_files.data]
            
            # Obtener tracking actual
            tracking_data = {t['url']: t for t in self.db_manager.get_content_tracking()}
            
            new_files = []
            updated_count = 0
            new_count = 0
            
            for content in content_list:
                should_update = False
                
                if content.url not in tracking_data:
                    # Contenido completamente nuevo
                    should_update = True
                    new_count += 1
                elif tracking_data[content.url]['content_hash'] != content.content_hash:
                    # Contenido modificado
                    should_update = True
                    updated_count += 1
                    
                    # Eliminar archivo anterior si existe
                    old_file_id = tracking_data[content.url].get('file_id')
                    if old_file_id and old_file_id in current_file_ids:
                        try:
                            self.client.beta.vector_stores.files.delete(
                                vector_store_id=self.vector_store_id,
                                file_id=old_file_id
                            )
                            self.client.files.delete(old_file_id)
                            logger.info(f"🗑️ Archivo anterior eliminado: {old_file_id}")
                        except Exception as e:
                            logger.warning(f"Error eliminando archivo anterior: {e}")
                
                if should_update:
                    # Crear nuevo archivo
                    file_id = self.create_document_file(content)
                    new_files.append(file_id)
                    
                    # Actualizar tracking
                    self.db_manager.save_content_tracking(content, file_id)
            
            # Añadir archivos nuevos al vector store
            if new_files:
                logger.info(f"📤 Subiendo {len(new_files)} archivos al vector store...")
                
                batch_response = self.client.beta.vector_stores.file_batches.create(
                    vector_store_id=self.vector_store_id,
                    file_ids=new_files
                )
                
                # Esperar a que se procesen
                logger.info("⏳ Esperando procesamiento de archivos...")
                while batch_response.status in ['in_progress', 'cancelling']:
                    time.sleep(2)
                    batch_response = self.client.beta.vector_stores.file_batches.retrieve(
                        vector_store_id=self.vector_store_id,
                        batch_id=batch_response.id
                    )
                
                if batch_response.status == 'completed':
                    logger.info(f"✅ Vector Store actualizado exitosamente!")
                    logger.info(f"   🆕 {new_count} documentos nuevos")
                    logger.info(f"   🔄 {updated_count} documentos actualizados")
                else:
                    logger.error(f"❌ Error en procesamiento: {batch_response.status}")
            else:
                logger.info("ℹ️ No hay cambios que actualizar")
            
            return {"new": new_count, "updated": updated_count, "total": len(new_files)}
            
        except Exception as e:
            logger.error(f"Error actualizando vector store: {e}")
            raise
    
    def get_response(self, user_message: str, external_id: str = None) -> Dict:
        """Obtiene respuesta del assistant usando thread persistente"""
        try:
            thread_id = None
            
            # Si hay external_id, buscar thread existente
            if external_id:
                thread_id = self.db_manager.get_thread_id(external_id)
            
            # Crear thread si no existe
            if not thread_id:
                thread = self.client.beta.threads.create()
                thread_id = thread.id
                logger.info(f"🆕 Nuevo thread creado: {thread_id}")
                
                # Guardar mapeo si hay external_id
                if external_id:
                    self.db_manager.save_thread_mapping(external_id, thread_id)
            else:
                logger.info(f"🔄 Usando thread existente: {thread_id}")
            
            # Añadir mensaje del usuario
            self.client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=user_message
            )
            
            # Ejecutar assistant
            run = self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            # Esperar respuesta
            max_wait_time = 60  # 60 segundos máximo
            wait_time = 0
            
            while run.status in ['queued', 'in_progress', 'cancelling'] and wait_time < max_wait_time:
                time.sleep(1)
                wait_time += 1
                run = self.client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
            
            if run.status == 'completed':
                # Obtener mensajes
                messages = self.client.beta.threads.messages.list(
                    thread_id=thread_id,
                    order='desc',
                    limit=1
                )
                
                if messages.data:
                    response_content = messages.data[0].content[0].text.value
                    
                    return {
                        "response": response_content,
                        "thread_id": thread_id,
                        "success": True
                    }
            
            logger.error(f"Run falló con status: {run.status}")
            return {
                "response": "Disculpá, tuve un problema técnico. Intentá de nuevo en un ratito.",
                "thread_id": thread_id,
                "success": False
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo respuesta: {e}")
            return {
                "response": "Uy, disculpá, tengo un problemita técnico. ¿Podés intentar de nuevo?",
                "thread_id": thread_id,
                "success": False
            }

class SchoolAssistantWithVectorStore:
    """Sistema principal que combina scraping + OpenAI Assistant"""
    
    def __init__(self, website_url: str, school_name: str):
        self.website_url = website_url
        self.school_name = school_name
        self.scraper = ImprovedWebScraper(website_url)
        self.assistant_manager = OpenAIAssistantManager(
            OPENAI_API_KEY, OPENAI_ASSISTANT_ID, OPENAI_VECTOR_STORE_ID, school_name
        )
        self.last_update = None
        logger.info("🎓 School Assistant con Vector Store inicializado")
    
    def update_knowledge_base(self):
        """Actualiza la base de conocimiento completa"""
        try:
            logger.info("🔄 Iniciando actualización de base de conocimiento...")
            
            # Scraping exhaustivo
            content_list = self.scraper.scrape_website_exhaustive(max_pages=100, max_depth=5)
            
            if not content_list:
                logger.warning("⚠️ No se obtuvo contenido del scraping")
                return {"error": "No content scraped"}
            
            # Actualizar vector store
            result = self.assistant_manager.update_vector_store_content(content_list)
            
            self.last_update = datetime.now()
            
            logger.info("✅ Base de conocimiento actualizada exitosamente")
            return {
                "success": True,
                "timestamp": self.last_update.isoformat(),
                "pages_scraped": len(content_list),
                "files_new": result["new"],
                "files_updated": result["updated"]
            }
            
        except Exception as e:
            logger.error(f"Error actualizando base de conocimiento: {e}")
            return {"error": str(e)}
    
    def get_response(self, user_message: str, external_id: str = None) -> Dict:
        """Obtiene respuesta del assistant"""
        return self.assistant_manager.get_response(user_message, external_id)
    
    def get_stats(self) -> Dict:
        """Obtiene estadísticas del sistema"""
        try:
            tracking_data = self.assistant_manager.db_manager.get_content_tracking()
            
            # Estadísticas del vector store
            vector_store = self.assistant_manager.client.beta.vector_stores.retrieve(
                self.assistant_manager.vector_store_id
            )
            
            return {
                "pages_tracked": len(tracking_data),
                "vector_
