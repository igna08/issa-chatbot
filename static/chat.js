(function() {
    // Configuración del widget
    const CHAT_CONFIG = {
        apiUrl: 'https://issa-chatbot-sij4.onrender.com/api/chat', // URL de tu backend Python
        position: 'bottom-right',
        theme: {
            primaryColor: '#1a1a1a',
            secondaryColor: '#2d2d2d',
            backgroundColor: '#ffffff',
            textColor: '#1a1a1a',
            accentColor: '#4f46e5'
        }
    };

    // Estilos CSS mejorados con mejor responsividad
    const widgetStyles = `
        #chat-widget-container * {
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        }
        
        #chat-widget-button {
            position: fixed;
            bottom: 24px;
            right: 24px;
            width: 60px;
            height: 60px;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            border: none;
            border-radius: 50%;
            cursor: pointer;
            z-index: 10000;
            box-shadow: 0 6px 24px rgba(26, 26, 26, 0.3), 0 2px 8px rgba(0, 0, 0, 0.1);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
        }
        
        #chat-widget-button:hover {
            transform: scale(1.05) translateY(-1px);
            box-shadow: 0 8px 28px rgba(26, 26, 26, 0.4), 0 4px 12px rgba(0, 0, 0, 0.15);
        }
        
        #chat-widget-button.active {
            background: linear-gradient(135deg, #2d2d2d 0%, #404040 100%);
        }
        
        #chat-widget-button svg {
            transition: all 0.3s ease;
        }
        
        /* Pulsing animation when closed */
        @keyframes pulse {
            0%, 100% { box-shadow: 0 8px 32px rgba(26, 26, 26, 0.3), 0 2px 8px rgba(0, 0, 0, 0.1); }
            50% { box-shadow: 0 8px 32px rgba(26, 26, 26, 0.5), 0 2px 8px rgba(0, 0, 0, 0.2); }
        }
        
        #chat-widget-button:not(.active) {
            animation: pulse 3s infinite;
        }
        
        #chat-widget-window {
            position: fixed;
            bottom: 108px;
            right: 24px;
            width: min(400px, calc(100vw - 48px));
            height: min(600px, calc(100vh - 140px));
            background: #ffffff;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15), 0 8px 24px rgba(0, 0, 0, 0.1);
            z-index: 9999;
            display: none;
            flex-direction: column;
            border: 1px solid rgba(229, 229, 229, 0.6);
            overflow: hidden;
            backdrop-filter: blur(10px);
        }
        
        #chat-widget-window.show {
            display: flex;
            animation: slideUpFade 0.5s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        @keyframes slideUpFade {
            from {
                opacity: 0;
                transform: translateY(40px) scale(0.95);
            }
            to {
                opacity: 1;
                transform: translateY(0) scale(1);
            }
        }
        
        .chat-header {
            padding: 24px;
            background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
            border-bottom: 1px solid rgba(229, 229, 229, 0.8);
            position: relative;
            overflow: hidden;
            flex-shrink: 0;
        }
        
        .chat-header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, #1a1a1a, #2d2d2d, #1a1a1a);
            animation: shimmer 2s infinite;
        }
        
        @keyframes shimmer {
            0% { transform: translateX(-100%); }
            100% { transform: translateX(100%); }
        }
        
        .chat-header-content {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-header h3 {
            margin: 0;
            font-size: 20px;
            font-weight: 700;
            color: #1a1a1a;
            letter-spacing: -0.02em;
        }
        
        .chat-status {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: #6b7280;
            margin-top: 4px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
            animation: breathe 2s infinite;
            box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);
        }
        
        @keyframes breathe {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        
        .chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background: linear-gradient(to bottom, #fafafa 0%, #f5f5f5 100%);
            scroll-behavior: smooth;
            min-height: 0;
        }
        
        .chat-messages::-webkit-scrollbar {
            width: 4px;
        }
        
        .chat-messages::-webkit-scrollbar-track {
            background: transparent;
        }
        
        .chat-messages::-webkit-scrollbar-thumb {
            background: #d1d5db;
            border-radius: 2px;
        }
        
        .chat-message {
            margin-bottom: 20px;
            display: flex;
            gap: 12px;
            animation: messageSlide 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        @keyframes messageSlide {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .chat-message.user {
            justify-content: flex-end;
        }
        
        .chat-message.assistant {
            justify-content: flex-start;
        }
        
        .message-content {
            max-width: 80%;
            padding: 14px 18px;
            border-radius: 20px;
            font-size: 14px;
            line-height: 1.5;
            word-wrap: break-word;
            position: relative;
            transition: all 0.2s ease;
        }
        
        .message-content:hover {
            transform: translateY(-1px);
        }
        
        .chat-message.user .message-content {
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #ffffff;
            border-bottom-right-radius: 8px;
            box-shadow: 0 4px 16px rgba(26, 26, 26, 0.2);
        }
        
        .chat-message.assistant .message-content {
            background: #ffffff;
            border: 1px solid rgba(229, 229, 229, 0.8);
            border-bottom-left-radius: 8px;
            color: #1a1a1a;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
        }
        
        /* Estilos para markdown en los mensajes */
        .message-content strong, .message-content b {
            font-weight: 700;
            color: inherit;
        }
        
        .message-content em, .message-content i {
            font-style: italic;
        }
        
        .message-content code {
            background: rgba(0, 0, 0, 0.1);
            padding: 2px 4px;
            border-radius: 4px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 13px;
        }
        
        .chat-message.user .message-content code {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .message-content ul, .message-content ol {
            margin: 6px 0;
            padding-left: 18px;
        }
        
        .message-content li {
            margin: 2px 0;
            line-height: 1.4;
        }
        
        .message-content h1, 
        .message-content h2, 
        .message-content h3 {
            margin-top: 12px;
            margin-bottom: 6px;
        }
        
        .message-content p {
            margin: 6px 0;
        }
        
        .typing-indicator {
            display: none;
            align-items: center;
            gap: 12px;
            color: #6b7280;
            font-size: 13px;
            margin-bottom: 16px;
            padding-left: 16px;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        .typing-dots {
            display: flex;
            gap: 4px;
        }
        
        .typing-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #9ca3af;
            animation: typing 1.4s infinite ease-in-out;
        }
        
        .typing-dot:nth-child(2) { animation-delay: 0.2s; }
        .typing-dot:nth-child(3) { animation-delay: 0.4s; }
        
        @keyframes typing {
            0%, 80%, 100% { 
                opacity: 0.3;
                transform: scale(0.8);
            }
            40% { 
                opacity: 1;
                transform: scale(1);
            }
        }
        
        .chat-input {
            padding: 20px;
            border-top: 1px solid rgba(229, 229, 229, 0.8);
            background: #ffffff;
            backdrop-filter: blur(10px);
            flex-shrink: 0;
        }
        
        .input-form {
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }
        
        .input-wrapper {
            flex: 1;
            position: relative;
        }
        
        .message-input {
            width: 100%;
            min-height: 44px;
            max-height: 120px;
            padding: 12px 16px;
            border: 2px solid #e5e7eb;
            border-radius: 22px;
            font-size: 14px;
            resize: none;
            outline: none;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            font-family: inherit;
            background: #fafafa;
        }
        
        .message-input:focus {
            border-color: #1a1a1a;
            background: #ffffff;
            box-shadow: 0 0 0 3px rgba(26, 26, 26, 0.1);
        }
        
        .message-input::placeholder {
            color: #9ca3af;
            transition: color 0.2s ease;
        }
        
        .message-input:focus::placeholder {
            color: #d1d5db;
        }
        
        .send-button {
            width: 44px;
            height: 44px;
            border: none;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #ffffff;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            flex-shrink: 0;
            box-shadow: 0 4px 16px rgba(26, 26, 26, 0.2);
            position: relative;
            overflow: hidden;
        }
        
        .send-button::before {
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(135deg, #2d2d2d 0%, #404040 100%);
            opacity: 0;
            transition: opacity 0.3s ease;
        }
        
        .send-button:hover:not(:disabled) {
            transform: scale(1.05) translateY(-1px);
            box-shadow: 0 6px 20px rgba(26, 26, 26, 0.3);
        }
        
        .send-button:hover:not(:disabled)::before {
            opacity: 1;
        }
        
        .send-button:active:not(:disabled) {
            transform: scale(0.95);
        }
        
        .send-button:disabled {
            background: #d1d5db;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }
        
        .send-button svg {
            z-index: 1;
            position: relative;
            transition: transform 0.2s ease;
        }
        
        .send-button:not(:disabled):hover svg {
            transform: translateX(1px);
        }
        
        .error-message {
            background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);
            color: #dc2626;
            padding: 12px 16px;
            border-radius: 12px;
            margin-bottom: 12px;
            font-size: 13px;
            border: 1px solid #f87171;
            animation: errorSlide 0.3s ease;
        }
        
        @keyframes errorSlide {
            from {
                opacity: 0;
                transform: translateX(-10px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            text-align: center;
            color: #6b7280;
            padding: 40px 20px;
        }
        
        .empty-state-icon {
            width: 48px;
            height: 48px;
            margin-bottom: 16px;
            opacity: 0.6;
        }
        
        .empty-state h4 {
            margin: 0 0 8px 0;
            font-size: 16px;
            font-weight: 600;
            color: #374151;
        }
        
        .empty-state p {
            margin: 0;
            font-size: 14px;
            line-height: 1.5;
        }
        
        /* Responsive Design Mejorado */
        @media (max-width: 640px) {
            #chat-widget-window {
                bottom: 90px;
                right: 16px;
                left: 16px;
                width: auto;
                height: min(70vh, 500px);
                border-radius: 16px;
                max-height: calc(100vh - 120px);
            }
            
            #chat-widget-button {
                bottom: 20px;
                right: 20px;
                width: 56px;
                height: 56px;
            }
            
            .chat-header {
                padding: 16px 20px;
            }
            
            .chat-header h3 {
                font-size: 18px;
            }
            
            .chat-messages {
                padding: 16px;
            }
            
            .message-content {
                max-width: 85%;
                padding: 12px 16px;
                font-size: 14px;
            }
            
            .chat-input {
                padding: 16px;
            }
            
            .empty-state {
                padding: 20px 16px;
            }
            
            .empty-state-icon {
                width: 40px;
                height: 40px;
            }
        }
        
        @media (max-width: 480px) {
            #chat-widget-window {
                bottom: 80px;
                right: 12px;
                left: 12px;
                height: min(65vh, 450px);
                border-radius: 12px;
            }
            
            #chat-widget-button {
                width: 52px;
                height: 52px;
                bottom: 16px;
                right: 16px;
            }
            
            .chat-header {
                padding: 14px 16px;
            }
            
            .chat-header h3 {
                font-size: 16px;
            }
            
            .chat-status {
                font-size: 12px;
            }
            
            .chat-messages {
                padding: 12px;
            }
            
            .message-content {
                max-width: 90%;
                padding: 10px 14px;
                font-size: 13px;
            }
            
            .chat-input {
                padding: 12px;
            }
            
            .input-form {
                gap: 8px;
            }
            
            .message-input {
                font-size: 16px; /* Evita zoom en iOS */
                min-height: 40px;
                padding: 10px 14px;
            }
            
            .send-button {
                width: 40px;
                height: 40px;
            }
        }
        
        @media (max-height: 600px) {
            #chat-widget-window {
                height: min(80vh, 400px);
            }
            
            .chat-messages {
                padding: 12px 16px;
            }
            
            .empty-state {
                padding: 20px;
            }
        }
        
        /* Landscape mobile */
        @media (max-width: 896px) and (max-height: 500px) and (orientation: landscape) {
            #chat-widget-window {
                height: min(85vh, 350px);
                bottom: 70px;
            }
            
            .chat-header {
                padding: 12px 16px;
            }
            
            .chat-messages {
                padding: 12px;
            }
            
            .chat-input {
                padding: 12px;
            }
        }
    `;

    // Función para parsear markdown completo
    function parseMarkdown(text) {
        // Primero, eliminar todas las marcas de citación
        text = text.replace(/【[^】]+】/g, '');
        text = text.replace(/\[[\d:†\w]+\]/g, '');
        text = text.replace(/【.*?】/g, '');
        
        return text
            // Títulos ### (h3)
            .replace(/^### (.*$)/gim, '<h3 style="font-size: 16px; font-weight: 700; margin: 10px 0 6px 0; color: inherit;">$1</h3>')
            // Títulos ## (h2)
            .replace(/^## (.*$)/gim, '<h2 style="font-size: 18px; font-weight: 700; margin: 12px 0 6px 0; color: inherit;">$1</h2>')
            // Títulos # (h1)
            .replace(/^# (.*$)/gim, '<h1 style="font-size: 20px; font-weight: 700; margin: 14px 0 8px 0; color: inherit;">$1</h1>')
            // Listas con viñetas
            .replace(/^\* (.*$)/gim, '<li style="margin: 2px 0; line-height: 1.4;">$1</li>')
            .replace(/^- (.*$)/gim, '<li style="margin: 2px 0; line-height: 1.4;">$1</li>')
            // Listas numeradas
            .replace(/^\d+\. (.*$)/gim, '<li style="margin: 2px 0; line-height: 1.4;">$1</li>')
            // Negrita con **texto** o __texto__
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/__(.*?)__/g, '<strong>$1</strong>')
            // Cursiva con *texto* o _texto_ (pero no si es parte de **)
            .replace(/(?<!\*)\*(?!\*)([^*]+)\*(?!\*)/g, '<em>$1</em>')
            .replace(/(?<!_)_(?!_)([^_]+)_(?!_)/g, '<em>$1</em>')
            // Código inline con `texto`
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            // Bloques de código con ```
            .replace(/```([\s\S]*?)```/g, '<pre style="background: rgba(0,0,0,0.05); padding: 12px; border-radius: 8px; overflow-x: auto; margin: 8px 0;"><code>$1</code></pre>')
            // Enlaces [texto](url)
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color: #4f46e5; text-decoration: underline;">$1</a>')
            // Saltos de línea (doble enter para párrafo nuevo)
            .replace(/\n\n/g, '</p><p style="margin: 6px 0;">')
            // Salto de línea simple
            .replace(/\n/g, '<br>')
            // Envolver en párrafo si no hay otros bloques
            .replace(/^(?!<[h|p|li|pre|ul|ol])/gim, '<p style="margin: 6px 0;">');
    }

    // Clase principal del widget
    class ChatWidget {
        constructor(config) {
            this.setInputState(false);
            this.addMessage(message, 'user');
            this.messageInput.value = '';
            this.autoResizeTextarea();
            this.showTypingIndicator();

            try {
                // Payload compatible con tu backend Python
                const payload = {
                    channel: "website",
                    externalId: this.userId,
                    from: this.userId,
                    timestamp: new Date().toISOString(),
                    type: "text",
                    body: message
                };

                const response = await fetch(this.config.apiUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify(payload)
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const data = await response.json();
                this.hideTypingIndicator();

                let responseText = '';
                if (data.text) {
                    responseText = data.text;
                } else {
                    responseText = 'Disculpá, tuve un problemita técnico. ¿Podés intentar de nuevo?';
                }

                this.addMessage(responseText, 'assistant');
                this.saveChatHistory();
                this.retryCount = 0;

            } catch (error) {
                console.error('Error:', error);
                this.hideTypingIndicator();
                
                if (this.retryCount < this.maxRetries) {
                    this.retryCount++;
                    this.showError(`Error de conexión. Reintentando... (${this.retryCount}/${this.maxRetries})`);
                    setTimeout(() => this.sendMessage(), 2000);
                    return;
                } else {
                    this.showError('Disculpá, tengo problemas para conectarme. Por favor, intentá más tarde o contactá directamente al colegio.');
                }
            } finally {
                this.setInputState(true);
            }
        }

        addMessage(text, type) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `chat-message ${type}`;

            const contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            
            // Parsear markdown si es mensaje del asistente
            if (type === 'assistant') {
                contentDiv.innerHTML = parseMarkdown(text);
            } else {
                contentDiv.textContent = text;
            }

            messageDiv.appendChild(contentDiv);
            this.messagesContainer.appendChild(messageDiv);

            // Agregar a historial
            this.chatHistory.push({ text, type, timestamp: Date.now() });
            this.scrollToBottom();
        }

        showTypingIndicator() {
            this.isTyping = true;
            this.typingIndicator.style.display = 'flex';
            this.scrollToBottom();
        }

        hideTypingIndicator() {
            this.isTyping = false;
            this.typingIndicator.style.display = 'none';
        }

        setInputState(enabled) {
            this.messageInput.disabled = !enabled;
            this.sendButton.disabled = !enabled;
            
            if (enabled) {
                this.messageInput.focus();
            }
        }

        showError(message) {
            const errorDiv = document.createElement('div');
            errorDiv.className = 'error-message';
            errorDiv.textContent = message;
            
            this.messagesContainer.appendChild(errorDiv);
            this.scrollToBottom();

            setTimeout(() => {
                if (errorDiv.parentNode) {
                    errorDiv.remove();
                }
            }, 5000);
        }

        autoResizeTextarea() {
            this.messageInput.style.height = 'auto';
            const newHeight = Math.min(this.messageInput.scrollHeight, 120);
            this.messageInput.style.height = newHeight + 'px';
        }

        scrollToBottom() {
            requestAnimationFrame(() => {
                this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
            });
        }

        getUserId() {
            let userId = localStorage.getItem('san-agustin-chat-user-id');
            if (!userId) {
                userId = 'user-' + Math.random().toString(36).substr(2, 9) + '-' + Date.now();
                localStorage.setItem('san-agustin-chat-user-id', userId);
            }
            return userId;
        }

        saveChatHistory() {
            try {
                const recentHistory = this.chatHistory.slice(-30);
                localStorage.setItem('san-agustin-chat-history', JSON.stringify(recentHistory));
            } catch (error) {
                console.warn('No se pudo guardar el historial del chat:', error);
            }
        }

        loadChatHistory() {
            try {
                const history = localStorage.getItem('san-agustin-chat-history');
                return history ? JSON.parse(history) : [];
            } catch (error) {
                console.warn('No se pudo cargar el historial del chat:', error);
                return [];
            }
        }

        restoreChatHistory() {
            this.messagesContainer.innerHTML = '';

            if (this.chatHistory.length === 0) {
                this.showEmptyState();
                return;
            }

            this.chatHistory.forEach(msg => {
                if (msg.text && msg.type) {
                    const messageDiv = document.createElement('div');
                    messageDiv.className = `chat-message ${msg.type}`;

                    const contentDiv = document.createElement('div');
                    contentDiv.className = 'message-content';
                    
                    // Parsear markdown si es mensaje del asistente
                    if (msg.type === 'assistant') {
                        contentDiv.innerHTML = parseMarkdown(msg.text);
                    } else {
                        contentDiv.textContent = msg.text;
                    }

                    messageDiv.appendChild(contentDiv);
                    this.messagesContainer.appendChild(messageDiv);
                }
            });

            this.scrollToBottom();
        }
    }

    // Inicializar el widget cuando el DOM esté listo
    function initChatWidget() {
        if (window.chatWidgetSanAgustin) {
            return;
        }
        
        window.chatWidgetSanAgustin = new ChatWidget(CHAT_CONFIG);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initChatWidget);
    } else {
        initChatWidget();
    }

    if (window.chatWidgetInitialized) {
        return;
    }
    window.chatWidgetInitialized = true;

})();.config = config;
            this.isOpen = false;
            this.userId = this.getUserId();
            this.chatHistory = this.loadChatHistory();
            this.isTyping = false;
            this.retryCount = 0;
            this.maxRetries = 3;
            this.init();
        }

        init() {
            this.injectStyles();
            this.createWidget();
            this.attachEventListeners();
            this.restoreChatHistory();
        }

        injectStyles() {
            if (!document.getElementById('chat-widget-styles')) {
                const styleSheet = document.createElement('style');
                styleSheet.id = 'chat-widget-styles';
                styleSheet.textContent = widgetStyles;
                document.head.appendChild(styleSheet);
            }
        }

        createWidget() {
            // Eliminar widget existente si existe
            const existing = document.getElementById('chat-widget-container');
            if (existing) {
                existing.remove();
            }

            const container = document.createElement('div');
            container.id = 'chat-widget-container';

            const button = document.createElement('button');
            button.id = 'chat-widget-button';
            button.innerHTML = `
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                </svg>
            `;

            const window = document.createElement('div');
            window.id = 'chat-widget-window';
            window.innerHTML = `
                <div class="chat-header">
                    <div class="chat-header-content">
                        <div>
                            <h3>Agustín</h3>
                            <div class="chat-status">
                                <div class="status-dot"></div>
                                <span>Asistente del Colegio San Agustín</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="chat-messages" id="chat-messages">
                </div>
                
                <div class="typing-indicator" id="typing-indicator">
                    <span>Agustín está escribiendo</span>
                    <div class="typing-dots">
                        <div class="typing-dot"></div>
                        <div class="typing-dot"></div>
                        <div class="typing-dot"></div>
                    </div>
                </div>
                
                <div class="chat-input">
                    <form class="input-form" id="chat-form">
                        <div class="input-wrapper">
                            <textarea 
                                class="message-input" 
                                id="message-input" 
                                placeholder="Escribí tu consulta sobre el colegio..."
                                rows="1"
                            ></textarea>
                        </div>
                        <button type="submit" class="send-button" id="send-button">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                                <line x1="22" y1="2" x2="11" y2="13"></line>
                                <polygon points="22,2 15,22 11,13 2,9 22,2"></polygon>
                            </svg>
                        </button>
                    </form>
                </div>
            `;

            container.appendChild(button);
            container.appendChild(window);
            document.body.appendChild(container);

            this.button = button;
            this.window = window;
            this.messagesContainer = document.getElementById('chat-messages');
            this.messageInput = document.getElementById('message-input');
            this.sendButton = document.getElementById('send-button');
            this.typingIndicator = document.getElementById('typing-indicator');
            this.chatForm = document.getElementById('chat-form');
        }

        showEmptyState() {
            if (this.chatHistory.length === 0) {
                this.messagesContainer.innerHTML = `
                    <div class="empty-state">
                        <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                        </svg>
                        <h4>¡Hola! Soy Agustín</h4>
                        <p>Tu asistente del Colegio San Agustín.<br>¿En qué te puedo ayudar hoy?</p>
                    </div>
                `;
            }
        }

        attachEventListeners() {
            this.button.addEventListener('click', (e) => {
                e.preventDefault();
                this.toggleChat();
            });

            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && this.isOpen) {
                    this.closeChat();
                }
            });

            this.chatForm.addEventListener('submit', (e) => {
                e.preventDefault();
                if (!this.isTyping) {
                    this.sendMessage();
                }
            });

            this.messageInput.addEventListener('input', () => {
                this.autoResizeTextarea();
            });

            this.messageInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey && !this.isTyping) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });

            // Cerrar al hacer clic fuera
            document.addEventListener('click', (e) => {
                if (this.isOpen && 
                    !this.window.contains(e.target) && 
                    !this.button.contains(e.target) &&
                    !e.target.closest('#chat-widget-container')) {
                    this.closeChat();
                }
            });
        }

        toggleChat() {
            if (this.isOpen) {
                this.closeChat();
            } else {
                this.openChat();
            }
        }

        openChat() {
            this.isOpen = true;
            this.window.classList.add('show');
            this.button.classList.add('active');
            
            setTimeout(() => {
                this.messageInput.focus();
                this.scrollToBottom();
            }, 100);
        }

        closeChat() {
            this.isOpen = false;
            this.window.classList.remove('show');
            this.button.classList.remove('active');
        }

        async sendMessage() {
            const message = this.messageInput.value.trim();
            if (!message || this.isTyping) return;

            // Limpiar estado vacío si existe
            const emptyState = this.messagesContainer.querySelector('.empty-state');
            if (emptyState) {
                emptyState.remove();
            }

            this
