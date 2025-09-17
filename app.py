# Principales correcciones necesarias:

# 1. PROBLEMA: El código de inicialización está dentro de if __name__ == "__main__"
# En Render, esto no se ejecuta cuando usas gunicorn
# SOLUCIÓN: Mover la inicialización fuera

import os
import sqlite3
from fastapi.responses import FileResponse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from openai import OpenAI
from datetime import datetime, timedelta
import hashlib
import json
import time
import schedule
from dataclasses import dataclass
from typing import List, Dict, Optional
import logging
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

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
    
    # ... resto de métodos sin cambios ...
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
        
        cursor.execute('SELECT url, title, content, content_hash, last_updated FROM web_content')
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
    
    def get_conversation_history(self, chat_id: str, limit: int = 10) -> List[Dict]:
        """Obtiene el historial de una conversación"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT role, content, timestamp 
            FROM messages 
            WHERE chat_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (chat_id, limit))
        
        messages = cursor.fetchall()
        conn.close()
        
        # Revertir orden para tener cronológico
        return [{"role": msg[0], "content": msg[1], "timestamp": msg[2]} 
                for msg in reversed(messages)]

class WebScraper:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.visited_urls = set()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def is_valid_url(self, url: str) -> bool:
        """Verifica si la URL es válida para scrapear"""
        try:
            parsed = urlparse(url)
            base_parsed = urlparse(self.base_url)
            
            return (parsed.netloc == base_parsed.netloc and 
                    url not in self.visited_urls and
                    not any(ext in url.lower() for ext in ['.pdf', '.jpg', '.png', '.gif', '.css', '.js']))
        except:
            return False
    
    def extract_text_content(self, soup: BeautifulSoup) -> str:
        """Extrae el contenido de texto relevante"""
        try:
            # Remover scripts, estilos y otros elementos no deseados
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()
            
            # Extraer texto de elementos principales
            content_selectors = ['main', 'article', '.content', '#content', 'body']
            content = ""
            
            for selector in content_selectors:
                elements = soup.select(selector)
                if elements:
                    content = elements[0].get_text(separator='\n', strip=True)
                    break
            
            if not content:
                content = soup.get_text(separator='\n', strip=True)
            
            # Limpiar contenido
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            return '\n'.join(lines)
        except Exception as e:
            logger.error(f"Error extrayendo contenido: {e}")
            return ""
    
    def scrape_page(self, url: str) -> Optional[WebContent]:
        """Scrapea una página individual"""
        try:
            logger.info(f"Scrapeando: {url}")
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Obtener título
            title_element = soup.find('title')
            title = title_element.get_text().strip() if title_element else url
            
            # Obtener contenido
            content = self.extract_text_content(soup)
            
            if content and len(content.strip()) > 100:  # Mínimo contenido útil
                content_hash = hashlib.md5(content.encode()).hexdigest()
                return WebContent(
                    url=url,
                    title=title,
                    content=content,
                    last_updated=datetime.now(),
                    content_hash=content_hash
                )
        
        except Exception as e:
            logger.error(f"Error scrapeando {url}: {e}")
            return None
    
    def find_internal_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Encuentra enlaces internos en la página"""
        links = []
        try:
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(current_url, href)
                
                if self.is_valid_url(full_url):
                    links.append(full_url)
        except Exception as e:
            logger.error(f"Error encontrando enlaces: {e}")
        
        return links
    
    def scrape_website(self) -> List[WebContent]:
        """Scrapea todo el sitio web"""
        content_list = []
        urls_to_visit = [self.base_url]
        max_pages = 20  # Reducir para evitar timeouts
        
        logger.info(f"Iniciando scraping de: {self.base_url}")
        
        while urls_to_visit and len(self.visited_urls) < max_pages:
            current_url = urls_to_visit.pop(0)
            
            if current_url in self.visited_urls:
                continue
            
            self.visited_urls.add(current_url)
            
            content = self.scrape_page(current_url)
            if content:
                content_list.append(content)
                logger.info(f"✓ Contenido extraído de: {current_url}")
                
                # Buscar más enlaces si no hemos encontrado demasiados
                if len(self.visited_urls) < max_pages:
                    try:
                        response = self.session.get(current_url, timeout=10)
                        soup = BeautifulSoup(response.content, 'html.parser')
                        new_links = self.find_internal_links(soup, current_url)
                        urls_to_visit.extend(new_links[:5])  # Limitar enlaces por página
                    except:
                        pass
            
            time.sleep(1)  # Ser respetuoso con el servidor
        
        logger.info(f"Scraping completado. {len(content_list)} páginas procesadas de {len(self.visited_urls)} visitadas.")
        return content_list

class SchoolAssistant:
    def __init__(self, openai_api_key: str, website_url: str, school_name: str = ""):
        logger.info("Inicializando SchoolAssistant...")
        self.client = OpenAI(api_key=openai_api_key)
        self.website_url = website_url
        self.school_name = school_name
        self.db_manager = DatabaseManager()
        self.scraper = WebScraper(website_url)
        self.system_prompt = self._build_system_prompt()
        logger.info("SchoolAssistant inicializado correctamente")
    
    def _build_system_prompt(self) -> str:
        """Construye el prompt del sistema con información del colegio"""
        try:
            content_list = self.db_manager.get_all_content()
            logger.info(f"Construyendo prompt con {len(content_list)} contenidos")
            
            # Organizar contenido por categorías
            knowledge_sections = []
            for content in content_list[:10]:  # Limitamos a 10 para no sobrecargar
                section = f"""
### {content.title}
{content.content[:1500]}{'...' if len(content.content) > 1500 else ''}
"""
                knowledge_sections.append(section)
            
            knowledge_base = "\n".join(knowledge_sections)
        except Exception as e:
            logger.error(f"Error construyendo prompt: {e}")
            knowledge_base = "Información del sitio web en proceso de carga..."
        
        return f"""Sos un asistente virtual del {self.school_name} y tu nombre es Agustín (por San Agustín). Sos argentino, amable y cordial. Tu objetivo es ayudar a las familias, estudiantes y visitantes de la mejor manera posible.

## TU PERSONALIDAD:
- **Argentino auténtico**: Hablás natural, usás "vos", "che", y expresiones típicas argentinas sin exagerar
- **Amable y cercano**: Tratás a todos con calidez, como si fueras un miembro más de la comunidad educativa  
- **Directo y claro**: Respondés exactamente lo que te preguntan, sin dar información de más
- **Preguntón cuando es necesario**: Si necesitás aclarar algo para dar una respuesta precisa, preguntás

## INFORMACIÓN DEL COLEGIO:
{knowledge_base}

## CÓMO RESPONDÉS:
1. **Saludá cordialmente** al inicio de cada conversación
2. **Escuchá bien** qué te están preguntando específicamente
3. **Respondé directamente** a la pregunta, sin dar vueltas
4. **Si no tenés la info exacta**, decilo honestamente y ofrecé alternativas
5. **Preguntá para aclarar** si la consulta no está clara
6. **Usá un lenguaje natural argentino** pero profesional

## EJEMPLOS DE TU FORMA DE HABLAR:
- "¡Hola! ¿Cómo andás? Soy Agustín, ¿en qué te puedo ayudar?"
- "Perfecto, te cuento sobre las inscripciones..."
- "Mirá, esa información específica no la tengo a mano, pero te puedo conectar con..."
- "¿Me podrías aclarar si te referís a primaria o secundaria?"
- "Dale, cualquier otra duda que tengas, preguntame nomás"

## LO QUE NO HACÉS:
- No tirás parrafadas largas si no te las piden
- No repetís información que ya diste
- No inventás datos que no tenés
- No usás un lenguaje demasiado formal o robótico

Recordá: cada familia que te habla está buscando el mejor lugar para su hijo. Tratá cada consulta con la importancia que se merece."""
    
    def update_content(self):
        """Actualiza el contenido del sitio web"""
        logger.info("Iniciando actualización de contenido...")
        
        try:
            new_content = self.scraper.scrape_website()
            existing_content = {c.url: c for c in self.db_manager.get_all_content()}
            
            updated_count = 0
            for content in new_content:
                if (content.url not in existing_content or 
                    existing_content[content.url].content_hash != content.content_hash):
                    self.db_manager.save_web_content(content)
                    updated_count += 1
            
            if updated_count > 0:
                self.system_prompt = self._build_system_prompt()
                logger.info(f"Contenido actualizado: {updated_count} páginas")
            else:
                logger.info("No hay cambios en el contenido")
        
        except Exception as e:
            logger.error(f"Error actualizando contenido: {e}")
    
    def get_response(self, chat_id: str, user_message: str, user_id: str = None) -> str:
        """Genera una respuesta para el usuario usando GPT-4"""
        try:
            # Crear conversación si no existe
            self.db_manager.create_conversation(chat_id, user_id)
            
            # Obtener historial
            history = self.db_manager.get_conversation_history(chat_id)
            
            # Construir mensajes para OpenAI
            messages = [{"role": "system", "content": self.system_prompt}]
            
            # Añadir historial reciente
            for msg in history[-8:]:  # Últimos 8 mensajes para mantener contexto
                messages.append({"role": msg["role"], "content": msg["content"]})
            
            # Añadir mensaje actual
            messages.append({"role": "user", "content": user_message})
            
            # Llamada a OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=400,  # Respuestas más concisas
                temperature=0.8,  # Más natural y conversacional
                presence_penalty=0.2,  # Evita repetición
                frequency_penalty=0.1   # Promueve variedad
            )
            
            assistant_response = response.choices[0].message.content
            
            # Guardar mensajes
            self.db_manager.save_message(chat_id, "user", user_message)
            self.db_manager.save_message(chat_id, "assistant", assistant_response)
            
            return assistant_response
            
        except Exception as e:
            logger.error(f"Error generando respuesta: {e}")
            return "Uy, disculpá, tengo un problemita técnico. ¿Podés intentar de nuevo en un ratito? Si sigue sin andar, mejor llamá directamente al colegio."

# API REST para el widget
app = Flask(__name__)
CORS(app)

# ======== INICIALIZACIÓN AUTOMÁTICA ========
# Esto se ejecuta cuando Flask/Gunicorn carga el módulo
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
        
        # Realizar scraping inicial en background para no bloquear
        try:
            logger.info("Realizando scraping inicial...")
            assistant.update_content()
            logger.info("✓ Sistema completamente listo")
        except Exception as e:
            logger.warning(f"Scraping inicial falló, continuando: {e}")
            # El asistente puede funcionar sin contenido inicial
        
        return True
    except Exception as e:
        logger.error(f"Error fatal inicializando asistente: {e}")
        return False

# Intentar inicializar inmediatamente cuando se carga el módulo
try:
    success = init_assistant()
    if not success:
        logger.error("Inicialización falló")
except Exception as e:
    logger.error(f"Error en inicialización automática: {e}")

# ======== ENDPOINTS ========
@app.route('/api/webhook/website', methods=['POST'])
def webhook_chat():
    """Endpoint compatible con el formato del widget"""
    global assistant
    
    if not assistant:
        logger.error("Assistant no inicializado - intentando reinicializar...")
        if not init_assistant():
            return jsonify({"text": "El asistente no está disponible en este momento. Por favor intenta más tarde."}), 500
    
    try:
        data = request.json
        logger.info(f"Received data: {data}")
        
        message_body = data.get('body', '')
        external_id = data.get('externalId', f"web_{int(time.time())}")
        
        if not message_body:
            return jsonify({"text": "Por favor escribí tu consulta."}), 400
        
        # Usar externalId como chat_id para mantener contexto por usuario
        response = assistant.get_response(external_id, message_body, external_id)
        
        logger.info(f"Generated response: {response}")
        
        return jsonify({
            "text": response,
            "type": "text"
        })
    
    except Exception as e:
        logger.error(f"Error en webhook endpoint: {e}")
        return jsonify({"text": "Disculpá, tuve un problemita técnico. Intentá de nuevo en un ratito."}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    """Endpoint alternativo para compatibilidad"""
    return webhook_chat()

@app.route('/api/health', methods=['GET'])
def health():
    """Endpoint de salud"""
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
            content_count = len(assistant.db_manager.get_all_content())
            status_info["content_pages"] = content_count
        except:
            status_info["content_pages"] = "error"
    
    return jsonify(status_info)

@app.route('/api/update-content', methods=['POST'])
def update_content():
    """Endpoint para actualizar contenido manualmente"""
    global assistant
    
    if not assistant:
        if not init_assistant():
            return jsonify({"error": "Asistente no inicializado"}), 500
    
    try:
        assistant.update_content()
        return jsonify({"message": "Contenido actualizado correctamente"})
    except Exception as e:
        logger.error(f"Error actualizando contenido: {e}")
        return jsonify({"error": "Error actualizando contenido"}), 500

@app.route('/api/reinit', methods=['POST'])
def reinit():
    """Endpoint para reinicializar el asistente"""
    global assistant
    try:
        assistant = None
        success = init_assistant()
        if success:
            return jsonify({"message": "Asistente reinicializado correctamente"})
        else:
            return jsonify({"error": "Error reinicializando asistente"}), 500
    except Exception as e:
        logger.error(f"Error reinicializando: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/chat.js")
def serve_chat():
    return send_from_directory("static", "chat.js", mimetype="application/javascript")

@app.route('/')
def home():
    """Página de inicio básica"""
    return jsonify({
        "message": "Agustín - Asistente del Colegio",
        "status": "running",
        "endpoints": {
            "chat": "/api/chat",
            "webhook": "/api/webhook/website",
            "health": "/api/health",
            "update": "/api/update-content"
        }
    })

if __name__ == "__main__":
    try:
        print("🌐 Servidor listo en http://localhost:5000")
        print("📱 API disponible en /api/chat")
        print("🏥 Health check en /api/health")
        
        app.run(host='0.0.0.0', port=5000, debug=False)
        
    except Exception as e:
        logger.error(f"Error fatal al inicializar servidor: {e}")
        print(f"❌ Error al inicializar: {e}")
        print("📋 Verifica que tu archivo .env tenga las variables correctas:")
        print("   - OPENAI_API_KEY=tu_api_key")
        print("   - WEBSITE_URL=https://tu-sitio.com")
        print("   - SCHOOL_NAME=Nombre del Colegio")