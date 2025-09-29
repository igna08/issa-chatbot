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
    
    def clear_content_tracking(self):
        """Limpia todo el tracking de contenido"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM web_content_tracking')
        conn.commit()
        conn.close()
        logger.info("Tracking de contenido limpiado")
    
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
        
        logger.info(f"Iniciando scraping exhaustivo de: {self.base_url}")
        logger.info(f"Límites: {max_pages} páginas máximo, {max_depth} niveles de profundidad")
        
        while current_depth < max_depth and len(content_list) < max_pages:
            if current_depth not in urls_by_depth or not urls_by_depth[current_depth]:
                current_depth += 1
                continue
            
            logger.info(f"Procesando nivel {current_depth} - {len(urls_by_depth[current_depth])} URLs")
            
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
        
        logger.info(f"Scraping completado: {len(content_list)} páginas útiles")
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
            assistant = self.client.assistants.retrieve(self.assistant_id)
            logger.info(f"✓ Assistant encontrado: {assistant.name}")
            
            vector_store = self.client.vector_stores.retrieve(self.vector_store_id)
            logger.info(f"✓ Vector Store encontrado: {vector_store.name}")
            
            self.db_manager.save_assistant_config(self.assistant_id, self.vector_store_id)
            
        except Exception as e:
            logger.error(f"Error verificando recursos: {e}")
            raise
    
    def create_document_file(self, content: WebContent) -> str:
        """Crea un archivo de documento para el vector store"""
        try:
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
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as temp_file:
                temp_file.write(document_content)
                temp_file_path = temp_file.name
            
            with open(temp_file_path, 'rb') as f:
                file_response = self.client.files.create(
                    file=f,
                    purpose='assistants'
                )
            
            os.unlink(temp_file_path)
            
            logger.info(f"✓ Archivo creado: {file_response.id} para {content.url}")
            return file_response.id
            
        except Exception as e:
            logger.error(f"Error creando archivo para {content.url}: {e}")
            raise
    
    def clean_vector_store(self):
        """Limpia completamente el Vector Store eliminando todos los archivos"""
        try:
            logger.info("Limpiando Vector Store...")
            
            # Obtener todos los archivos del vector store
            files_response = self.client.vector_stores.files.list(
                vector_store_id=self.vector_store_id,
                limit=100
            )
            
            deleted_count = 0
            failed_count = 0
            
            # Eliminar cada archivo del vector store Y de OpenAI Files
            for file in files_response.data:
                try:
                    # Primero eliminar del vector store
                    self.client.vector_stores.files.delete(
                        vector_store_id=self.vector_store_id,
                        file_id=file.id
                    )
                    
                    # Luego eliminar el archivo completamente de OpenAI
                    self.client.files.delete(file.id)
                    
                    deleted_count += 1
                    logger.info(f"Archivo eliminado: {file.id}")
                    
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Error eliminando archivo {file.id}: {e}")
            
            # Si hay más archivos (paginación), continuar
            while files_response.has_more:
                files_response = self.client.vector_stores.files.list(
                    vector_store_id=self.vector_store_id,
                    after=files_response.last_id,
                    limit=100
                )
                
                for file in files_response.data:
                    try:
                        self.client.vector_stores.files.delete(
                            vector_store_id=self.vector_store_id,
                            file_id=file.id
                        )
                        self.client.files.delete(file.id)
                        deleted_count += 1
                        logger.info(f"Archivo eliminado: {file.id}")
                    except Exception as e:
                        failed_count += 1
                        logger.warning(f"Error eliminando archivo {file.id}: {e}")
            
            # Limpiar también el tracking local
            self.db_manager.clear_content_tracking()
            
            logger.info(f"Vector Store limpiado: {deleted_count} archivos eliminados, {failed_count} fallos")
            return {"deleted": deleted_count, "failed": failed_count}
            
        except Exception as e:
            logger.error(f"Error limpiando Vector Store: {e}")
            raise
    
    def update_vector_store_content(self, content_list: List[WebContent]):
        """Actualiza el vector store LIMPIÁNDOLO primero y subiendo contenido fresco"""
        try:
            logger.info(f"Actualizando vector store con {len(content_list)} documentos...")
            
            # PASO 1: Limpiar completamente el Vector Store
            logger.info("PASO 1: Limpiando Vector Store existente...")
            clean_result = self.clean_vector_store()
            logger.info(f"Vector Store limpiado: {clean_result['deleted']} archivos eliminados")
            
            # PASO 2: Subir todos los nuevos archivos
            logger.info("PASO 2: Subiendo contenido actualizado...")
            new_file_ids = []
            
            for content in content_list:
                try:
                    # Crear nuevo archivo
                    file_id = self.create_document_file(content)
                    new_file_ids.append(file_id)
                    
                    # Guardar en tracking
                    self.db_manager.save_content_tracking(content, file_id)
                    
                except Exception as e:
                    logger.error(f"Error procesando {content.url}: {e}")
            
            # PASO 3: Añadir archivos al vector store en batch
            if new_file_ids:
                logger.info(f"PASO 3: Añadiendo {len(new_file_ids)} archivos al Vector Store...")
                
                batch_response = self.client.vector_stores.file_batches.create(
                    vector_store_id=self.vector_store_id,
                    file_ids=new_file_ids
                )
                
                # Esperar procesamiento
                logger.info("Esperando procesamiento de archivos...")
                while batch_response.status in ['in_progress', 'cancelling']:
                    time.sleep(2)
                    batch_response = self.client.vector_stores.file_batches.retrieve(
                        vector_store_id=self.vector_store_id,
                        batch_id=batch_response.id
                    )
                    logger.info(f"Estado: {batch_response.status} - {batch_response.file_counts.completed}/{batch_response.file_counts.total} archivos procesados")
                
                if batch_response.status == 'completed':
                    logger.info("Vector Store actualizado exitosamente!")
                    logger.info(f"Total de documentos nuevos: {len(new_file_ids)}")
                else:
                    logger.error(f"Error en procesamiento: {batch_response.status}")
            else:
                logger.warning("No se generaron archivos para subir")
            
            return {
                "new": len(new_file_ids), 
                "updated": 0,  # Ya no hay "actualizados", todo es nuevo
                "total": len(new_file_ids),
                "cleaned": clean_result['deleted']
            }
            
        except Exception as e:
            logger.error(f"Error actualizando vector store: {e}")
            raise
    
    def get_response(self, user_message: str, external_id: str = None) -> Dict:
        """Obtiene respuesta del assistant usando thread persistente"""
        try:
            thread_id = None
            
            if external_id:
                thread_id = self.db_manager.get_thread_id(external_id)
            
            if not thread_id:
                thread = self.client.beta.threads.create()
                thread_id = thread.id
                logger.info(f"Nuevo thread creado: {thread_id}")
                
                if external_id:
                    self.db_manager.save_thread_mapping(external_id, thread_id)
            else:
                logger.info(f"Usando thread existente: {thread_id}")
            
            self.client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=user_message
            )
            
            run = self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            max_wait_time = 60
            wait_time = 0
            
            while run.status in ['queued', 'in_progress', 'cancelling'] and wait_time < max_wait_time:
                time.sleep(1)
                wait_time += 1
                run = self.client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
            
            if run.status == 'completed':
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
        logger.info("School Assistant con Vector Store inicializado")
    
    def update_knowledge_base(self):
        """Actualiza la base de conocimiento completa"""
        try:
            logger.info("Iniciando actualización de base de conocimiento...")
            
            # Scraping exhaustivo
            content_list = self.scraper.scrape_website_exhaustive(max_pages=100, max_depth=5)
            
            if not content_list:
                logger.warning("No se obtuvo contenido del scraping")
                return {"error": "No content scraped"}
            
            # Actualizar vector store (incluye limpieza automática)
            result = self.assistant_manager.update_vector_store_content(content_list)
            
            self.last_update = datetime.now()
            
            logger.info("Base de conocimiento actualizada exitosamente")
            return {
                "success": True,
                "timestamp": self.last_update.isoformat(),
                "pages_scraped": len(content_list),
                "files_new": result["new"],
                "files_cleaned": result["cleaned"]
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
                "vector_store_files": vector_store.file_counts.total,
                "last_update": self.last_update.isoformat() if self.last_update else None,
                "visited_urls": len(self.scraper.visited_urls),
                "failed_urls": len(self.scraper.failed_urls)
            }
        except Exception as e:
            logger.error(f"Error obteniendo estadísticas: {e}")
            return {"error": str(e)}

# ======== FLASK API ========
app = Flask(__name__)
CORS(app)

# Variable global para el asistente
assistant = None

def init_assistant():
    """Inicializa el asistente con vector store"""
    global assistant
    
    try:
        logger.info("Inicializando Agustín con OpenAI Assistant + Vector Store...")
        logger.info(f"Configuración:")
        logger.info(f"   - URL: {WEBSITE_URL}")
        logger.info(f"   - Escuela: {SCHOOL_NAME}")
        logger.info(f"   - Assistant ID: {OPENAI_ASSISTANT_ID}")
        logger.info(f"   - Vector Store ID: {OPENAI_VECTOR_STORE_ID}")
        
        assistant = SchoolAssistantWithVectorStore(WEBSITE_URL, SCHOOL_NAME)
        
        # Actualización inicial
        logger.info("Realizando actualización inicial...")
        result = assistant.update_knowledge_base()
        
        if result.get("success"):
            logger.info("Sistema completamente listo!")
            return True
        else:
            logger.warning(f"Actualización inicial con problemas: {result}")
            return True
            
    except Exception as e:
        logger.error(f"Error inicializando asistente: {e}")
        return False

# Inicializar automáticamente
try:
    success = init_assistant()
    if not success:
        logger.error("Inicialización falló")
except Exception as e:
    logger.error(f"Error en inicialización automática: {e}")

# ======== ENDPOINTS ========

@app.route('/api/webhook/website', methods=['POST'])
def webhook_chat():
    """Endpoint principal para chat con OpenAI Assistant"""
    global assistant
    
    if not assistant:
        logger.error("Assistant no inicializado")
        if not init_assistant():
            return jsonify({"text": "El asistente no está disponible. Por favor intenta más tarde."}), 500
    
    try:
        data = request.json
        logger.info(f"Datos recibidos: {data}")
        
        message_body = data.get('body', '').strip()
        external_id = data.get('externalId', f"web_{int(time.time())}")
        
        if not message_body:
            return jsonify({"text": "Por favor escribí tu consulta."}), 400
        
        result = assistant.get_response(message_body, external_id)
        
        logger.info(f"Respuesta generada para {external_id}: {result['response'][:100]}...")
        
        return jsonify({
            "text": result["response"],
            "type": "text",
            "thread_id": result["thread_id"],
            "success": result["success"],
            "timestamp": datetime.now().isoformat()
        })
    
    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        return jsonify({"text": "Disculpá, tuve un problema técnico. Intentá de nuevo en un ratito."}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    """Endpoint alternativo para chat"""
    return webhook_chat()

@app.route('/api/update-knowledge', methods=['POST'])
def update_knowledge():
    """Endpoint para actualizar base de conocimiento manualmente"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        logger.info("Actualización manual de base de conocimiento solicitada")
        result = assistant.update_knowledge_base()
        
        if result.get("success"):
            return jsonify({
                "message": "Base de conocimiento actualizada exitosamente (Vector Store limpiado y renovado)",
                "result": result
            })
        else:
            return jsonify({
                "message": "Error actualizando base de conocimiento",
                "result": result
            }), 500
            
    except Exception as e:
        logger.error(f"Error actualizando conocimiento: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    """Health check completo del sistema"""
    global assistant
    
    status = {
        "status": "ok" if assistant else "error",
        "timestamp": datetime.now().isoformat(),
        "assistant_initialized": assistant is not None,
        "environment": {
            "openai_api_key": "✓ Configurada" if OPENAI_API_KEY else "✗ Falta",
            "website_url": "✓ Configurada" if WEBSITE_URL else "✗ Falta",
            "assistant_id": "✓ Configurado" if OPENAI_ASSISTANT_ID else "✗ Falta",
            "vector_store_id": "✓ Configurado" if OPENAI_VECTOR_STORE_ID else "✗ Falta",
            "school_name": SCHOOL_NAME
        }
    }
    
    if assistant:
        try:
            stats = assistant.get_stats()
            status["stats"] = stats
            
            try:
                assistant_info = assistant.assistant_manager.client.beta.assistants.retrieve(
                    OPENAI_ASSISTANT_ID
                )
                status["openai_connection"] = "✓ Conectado"
                status["assistant_name"] = assistant_info.name
            except Exception as e:
                status["openai_connection"] = f"✗ Error: {str(e)}"
                
        except Exception as e:
            status["stats_error"] = str(e)
    
    return jsonify(status)

@app.route('/api/reinit', methods=['POST'])
def reinit():
    """Reinicializar sistema completo"""
    global assistant
    
    try:
        logger.info("Reinicialización manual solicitada")
        assistant = None
        success = init_assistant()
        
        if success:
            stats = assistant.get_stats() if assistant else {}
            return jsonify({
                "message": "Sistema reinicializado exitosamente",
                "timestamp": datetime.now().isoformat(),
                "stats": stats
            })
        else:
            return jsonify({"error": "Error reinicializando sistema"}), 500
    
    except Exception as e:
        logger.error(f"Error reinicializando: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/threads/<external_id>/clear', methods=['POST'])
def clear_thread(external_id):
    """Limpia un thread específico (inicia conversación nueva)"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        conn = sqlite3.connect(assistant.assistant_manager.db_manager.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM conversation_threads WHERE external_id = ?', (external_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            "message": f"Thread para {external_id} eliminado. Próxima conversación será nueva.",
            "external_id": external_id
        })
        
    except Exception as e:
        logger.error(f"Error limpiando thread: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/threads', methods=['GET'])
def list_threads():
    """Lista todos los threads activos"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        conn = sqlite3.connect(assistant.assistant_manager.db_manager.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT external_id, thread_id, created_at, last_activity 
            FROM conversation_threads 
            ORDER BY last_activity DESC 
            LIMIT 50
        ''')
        
        threads = []
        for row in cursor.fetchall():
            threads.append({
                "external_id": row[0],
                "thread_id": row[1],
                "created_at": row[2],
                "last_activity": row[3]
            })
        
        conn.close()
        
        return jsonify({
            "threads": threads,
            "total": len(threads)
        })
        
    except Exception as e:
        logger.error(f"Error listando threads: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/vector-store/info', methods=['GET'])
def vector_store_info():
    """Información del vector store"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        vector_store = assistant.assistant_manager.client.beta.vector_stores.retrieve(
            OPENAI_VECTOR_STORE_ID
        )
        
        files = assistant.assistant_manager.client.beta.vector_stores.files.list(
            vector_store_id=OPENAI_VECTOR_STORE_ID,
            limit=10
        )
        
        return jsonify({
            "vector_store": {
                "id": vector_store.id,
                "name": vector_store.name,
                "status": vector_store.status,
                "file_counts": {
                    "total": vector_store.file_counts.total,
                    "in_progress": vector_store.file_counts.in_progress,
                    "completed": vector_store.file_counts.completed,
                    "failed": vector_store.file_counts.failed,
                    "cancelled": vector_store.file_counts.cancelled
                },
                "created_at": vector_store.created_at,
                "last_active_at": vector_store.last_active_at
            },
            "recent_files": [
                {
                    "id": f.id,
                    "status": f.status,
                    "created_at": f.created_at,
                    "last_error": f.last_error.message if f.last_error else None
                }
                for f in files.data
            ]
        })
        
    except Exception as e:
        logger.error(f"Error obteniendo info del vector store: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/vector-store/clean', methods=['POST'])
def clean_vector_store():
    """Endpoint para limpiar manualmente el Vector Store"""
    global assistant
    
    if not assistant:
        return jsonify({"error": "Assistant not initialized"}), 500
    
    try:
        logger.info("Limpieza manual del Vector Store solicitada")
        result = assistant.assistant_manager.clean_vector_store()
        
        return jsonify({
            "message": "Vector Store limpiado exitosamente",
            "result": result,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error limpiando Vector Store: {e}")
        return jsonify({"error": str(e)}), 500
        
@app.route("/chat.js")
def serve_chat():
    return send_from_directory("static", "chat.js", mimetype="application/javascript")

@app.route('/')
def home():
    """Página principal con información del sistema"""
    global assistant
    
    stats = {}
    if assistant:
        try:
            stats = assistant.get_stats()
        except:
            stats = {"error": "Error obteniendo estadísticas"}
    
    return jsonify({
        "message": f"Agustín - Asistente de {SCHOOL_NAME}",
        "version": "2.0 - OpenAI Assistant + Vector Store (Con limpieza automática)",
        "status": "running",
        "features": [
            "OpenAI Assistant nativo integrado",
            "Vector Store con limpieza automática en cada actualización",
            "Conversaciones persistentes por thread",
            "Scraping exhaustivo automatizado",
            "Actualización automática de conocimiento",
            "Sistema de tracking de contenido",
            "Eliminación de archivos duplicados"
        ],
        "endpoints": {
            "chat": "/api/chat",
            "webhook": "/api/webhook/website",
            "update_knowledge": "/api/update-knowledge",
            "health": "/api/health",
            "reinit": "/api/reinit",
            "threads": "/api/threads",
            "clear_thread": "/api/threads/<external_id>/clear",
            "vector_store_info": "/api/vector-store/info",
            "clean_vector_store": "/api/vector-store/clean"
        },
        "configuration": {
            "assistant_id": OPENAI_ASSISTANT_ID,
            "vector_store_id": OPENAI_VECTOR_STORE_ID,
            "school_name": SCHOOL_NAME,
            "website_url": WEBSITE_URL
        },
        "stats": stats
    })

# ======== TAREAS PROGRAMADAS ========
def scheduled_update():
    """Actualización programada cada 6 horas"""
    global assistant
    
    if assistant:
        try:
            logger.info("Ejecutando actualización programada...")
            result = assistant.update_knowledge_base()
            logger.info(f"Actualización programada completada: {result}")
        except Exception as e:
            logger.error(f"Error en actualización programada: {e}")

schedule.every(6).hours.do(scheduled_update)

def run_scheduler():
    """Ejecuta el scheduler en background"""
    while True:
        schedule.run_pending()
        time.sleep(60)

scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    try:
        print("=" * 60)
        print("AGUSTÍN - ASISTENTE EDUCATIVO v2.0")
        print("=" * 60)
        print("Servidor iniciado en http://localhost:5000")
        print("OpenAI Assistant + Vector Store integrado")
        print("Base de conocimiento actualizable automáticamente")
        print("Conversaciones persistentes por thread")
        print("Actualizaciones automáticas cada 6 horas")
        print("LIMPIEZA AUTOMÁTICA: Vector Store se renueva en cada actualización")
        print("-" * 60)
        print("CONFIGURACIÓN:")
        print(f"   • Assistant ID: {OPENAI_ASSISTANT_ID}")
        print(f"   • Vector Store ID: {OPENAI_VECTOR_STORE_ID}")
        print(f"   • Escuela: {SCHOOL_NAME}")
        print(f"   • Website: {WEBSITE_URL}")
        print("-" * 60)
        print("ENDPOINTS DISPONIBLES:")
        print("   • Chat: /api/webhook/website")
        print("   • Health: /api/health")
        print("   • Actualizar: /api/update-knowledge")
        print("   • Threads: /api/threads")
        print("   • Vector Store: /api/vector-store/info")
        print("   • Limpiar Vector Store: /api/vector-store/clean")
        print("=" * 60)
        
        app.run(host='0.0.0.0', port=5000, debug=False)
        
    except Exception as e:
        logger.error(f"Error fatal al inicializar servidor: {e}")
        print(f"Error al inicializar: {e}")
        print("Verifica tu archivo .env con las variables necesarias:")
