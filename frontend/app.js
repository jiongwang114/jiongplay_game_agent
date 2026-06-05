/**
 * Frontend interaction logic for the Steam Game Recommendation Agent.
 *
 * Features:
 *   - Persistent session ID (localStorage)
 *   - SSE streaming from POST /chat
 *   - Game card rendering from parsed LLM replies
 *   - Enter‑key send
 */

// ---------------------------------------------------------------------------
//  Session management
// ---------------------------------------------------------------------------

function generateSessionId() {
    const stored = localStorage.getItem("steam_agent_session_id");
    if (stored) return stored;
    const id = "sess_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("steam_agent_session_id", id);
    return id;
}

const SESSION_ID = generateSessionId();

// ---------------------------------------------------------------------------
//  DOM refs
// ---------------------------------------------------------------------------

const chatContainer = document.getElementById("chat-container");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");

// ---------------------------------------------------------------------------
//  Helpers
// ---------------------------------------------------------------------------

/** Create and append a message bubble. */
function addBubble(role, content = "") {
    const div = document.createElement("div");
    div.className = `message message-${role === "user" ? "user" : "bot"}`;
    div.innerHTML = content;
    chatContainer.appendChild(div);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return div;
}

/** Create a game card from a data object. */
function renderGameCard(game) {
    const price = game.price || 0;
    const reviewPct = game.review ? Math.round(game.review * 100) : null;
    const tags = game.tags || [];

    const card = document.createElement("div");
    card.className = "game-card";

    const imgSrc = game.header_image || "";
    const tagsHtml = tags.slice(0, 3).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("");

    card.innerHTML = `
        ${imgSrc ? `<img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(game.name)}" loading="lazy">` : ""}
        <div class="card-body">
            <div class="card-name" title="${escapeHtml(game.name)}">${escapeHtml(game.name)}</div>
            <div class="card-price ${price === 0 ? "free" : ""}">
                ${price === 0 ? "免费" : "¥" + price}
            </div>
            ${reviewPct !== null ? `<div class="card-review">👍 ${reviewPct}% 好评</div>` : ""}
            ${tagsHtml ? `<div class="card-tags">${tagsHtml}</div>` : ""}
            ${game.store_url ? `<a class="card-link" href="${escapeHtml(game.store_url)}" target="_blank">查看商店 →</a>` : ""}
        </div>
    `;

    return card;
}

/** Simple HTML escaping. */
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
//  SSE streaming
// ---------------------------------------------------------------------------

async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;

    // Disable input while waiting
    messageInput.value = "";
    messageInput.disabled = true;
    sendBtn.disabled = true;

    // Show user bubble
    addBubble("user", escapeHtml(text));

    // Create bot bubble placeholder
    const botBubble = addBubble("bot", "⏳ 思考中...");

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: SESSION_ID, message: text }),
        });

        if (!response.ok) {
            botBubble.innerHTML = `❌ 请求失败 (${response.status})`;
            return;
        }

        // Read SSE stream
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = "";
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Parse SSE lines
            const lines = buffer.split("\n");
            buffer = lines.pop(); // keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const payload = line.slice(6);

                if (payload === "[DONE]") break;

                // Unescape the token
                const token = payload.replace(/\\n/g, "\n");
                fullText += token;

                // Update the bubble with rendered markdown-like content
                botBubble.innerHTML = renderMarkdown(fullText);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
        }

        // After the stream ends, try to parse game cards from the reply
        const gameData = parseGameData(fullText);
        if (gameData.length > 0) {
            const cardsArea = document.createElement("div");
            cardsArea.id = "game-cards";
            gameData.forEach(g => cardsArea.appendChild(renderGameCard(g)));
            chatContainer.appendChild(cardsArea);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

    } catch (err) {
        botBubble.innerHTML = `❌ 连接错误：${escapeHtml(err.message)}`;
    } finally {
        messageInput.disabled = false;
        sendBtn.disabled = false;
        messageInput.focus();
    }
}

// ---------------------------------------------------------------------------
//  Simple markdown‑ish rendering
// ---------------------------------------------------------------------------

function renderMarkdown(text) {
    let html = escapeHtml(text);
    // Bold: **text**
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Newlines → <br>
    html = html.replace(/\n/g, "<br>");
    return html;
}

// ---------------------------------------------------------------------------
//  Game data extraction from LLM reply
// ---------------------------------------------------------------------------

/**
 * The bot reply contains game recommendations in text form.
 * We attempt to extract structured data from the tool_results context
 * that was sent alongside the streaming response.
 *
 * For now, we parse game names from bold markers and try to match them
 * against a client‑side cache.  In a full implementation you could add a
 * GET /tool-results endpoint that returns the actual game data used by the
 * current LLM response.
 */
function parseGameData(text) {
    // Extract bold game names — the Agent prompts LLM to use **GameName**
    const namePattern = /\*\*(.+?)\*\*/g;
    const names = [];
    let match;
    while ((match = namePattern.exec(text)) !== null) {
        const name = match[1].trim();
        // Skip non‑game bold text like labels
        if (name.length < 2 || name.includes("：") || name.includes(":")) continue;
        if (name.includes("元") || name.includes("免费") || name.includes("¥")) continue;
        if (!names.includes(name)) names.push(name);
    }

    // For the MVP we return name‑only data — the server could be extended to
    // return structured game data alongside the stream.
    return names.map(name => ({
        name,
        price: null,
        review: null,
        tags: [],
        header_image: "",
        store_url: `https://store.steampowered.com/search/?term=${encodeURIComponent(name)}`,
    }));
}

// ---------------------------------------------------------------------------
//  Event listeners
// ---------------------------------------------------------------------------

sendBtn.addEventListener("click", sendMessage);

messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Auto‑focus the input on load
messageInput.focus();
