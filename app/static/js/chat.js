// Chat module for ChatGPT-style document chat interface

document.addEventListener('DOMContentLoaded', () => {
    initChatInterface();
});

function initChatInterface() {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const messagesContainer = document.getElementById('chat-messages');
    
    if (!chatForm || !chatInput || !messagesContainer) return;
    
    // Scroll to bottom on initial load
    scrollToBottom();
    
    // Form submit listener
    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const message = chatInput.value.trim();
        if (!message) return;
        
        // Disable input while sending
        chatInput.disabled = true;
        chatInput.value = '';
        
        // 1. Append User Message
        appendMessage(message, 'user');
        scrollToBottom();
        
        // 2. Append Typing Indicator
        const typingIndicatorId = appendTypingIndicator();
        scrollToBottom();
        
        // Get document ID from HTML attribute
        const docId = chatForm.getAttribute('data-doc-id');
        
        // 3. Send API Call
        fetch(`/api/document/${docId}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message: message })
        })
        .then(res => {
            if (!res.ok) throw new Error('Network response not ok');
            return res.json();
        })
        .then(data => {
            // Remove typing indicator
            removeTypingIndicator(typingIndicatorId);
            
            // Append AI Answer
            if (data.answer) {
                appendMessage(data.answer, 'ai');
            } else if (data.error) {
                appendMessage(`Error: ${data.error}`, 'ai');
            }
            scrollToBottom();
            chatInput.disabled = false;
            chatInput.focus();
        })
        .catch(err => {
            console.error(err);
            removeTypingIndicator(typingIndicatorId);
            appendMessage('Connection error. Failed to reach AI services.', 'ai');
            scrollToBottom();
            chatInput.disabled = false;
            chatInput.focus();
        });
    });
}

function appendMessage(text, sender) {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${sender}`;

    const avatar = document.createElement('div');
    avatar.className = 'bubble-avatar';
    avatar.innerHTML = sender === 'ai'
        ? '<i class="bi bi-robot"></i>'
        : '<i class="bi bi-person-fill"></i>';

    const textEl = document.createElement('div');
    textEl.className = 'bubble-text';

    // Convert newlines to breaks or format basic markdown (if from AI)
    if (sender === 'ai') {
        textEl.innerHTML = formatMarkdown(text);
    } else {
        textEl.textContent = text;
    }

    bubble.appendChild(avatar);
    bubble.appendChild(textEl);
    
    container.appendChild(bubble);
}

function appendTypingIndicator() {
    const container = document.getElementById('chat-messages');
    if (!container) return null;
    
    const indicatorId = 'typing-' + Date.now();
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble ai bubble-typing';
    bubble.id = indicatorId;
    
    bubble.innerHTML = `
        <div class="bubble-avatar"><i class="bi bi-robot"></i></div>
        <div class="bubble-text typing-indicator">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        </div>
    `;
    
    container.appendChild(bubble);
    return indicatorId;
}

function removeTypingIndicator(id) {
    if (!id) return;
    const indicator = document.getElementById(id);
    if (indicator) {
        indicator.remove();
    }
}

function scrollToBottom() {
    const container = document.getElementById('chat-messages');
    if (container) {
        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }
}

// Ensure send button is disabled while awaiting response
function setSendingState(isSending) {
    const sendBtn = document.getElementById('chat-send-btn');
    if (sendBtn) sendBtn.disabled = isSending;
    const input = document.getElementById('chat-input');
    if (input) input.disabled = isSending;
}

/**
 * Very basic client-side markdown formatter for list items, quotes, and code terms
 */
function formatMarkdown(text) {
    if (!text) return '';
    
    let html = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
        
    // Format bold (**text** or __text__)
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    
    // Format code block terms (`term`)
    html = html.replace(/`(.*?)`/g, '<code class="bg-dark text-light px-1 rounded">$1</code>');
    
    // Format lists starting with bullet points (• or * or -)
    html = html.replace(/^•\s+(.*?)$/gm, '<li>$1</li>');
    html = html.replace(/^\*\s+(.*?)$/gm, '<li>$1</li>');
    html = html.replace(/^-\s+(.*?)$/gm, '<li>$1</li>');
    
    // Wrap consecutive list items in <ul>
    html = html.replace(/(<li>.*?<\/li>)+/g, '<ul class="my-2">$1</ul>');
    
    // Convert remaining newlines to paragraphs
    html = html.split('\n\n').map(p => {
        if (p.startsWith('<ul') || p.startsWith('<li')) return p;
        return `<p class="mb-2">${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');
    
    return html;
}
