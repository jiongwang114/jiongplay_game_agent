/**
 * Steam Game Recommendation Agent — Frontend Logic
 *
 * Integrates with:
 *   - POST /chat                  SSE streaming AI responses
 *   - GET  /api/tool-results/{id}  Structured game data for card rendering
 *   - POST /api/sync-steam         Steam library sync simulation
 *   - GET  /api/agent-status       Agent status panel data
 *
 * Features:
 *   - Persistent session ID (localStorage)
 *   - SSE streaming with real-time markdown rendering
 *   - Rich game cards from structured tool results
 *   - Sidebar sync button with loading / success states
 *   - Quick prompt chips above the input
 *   - Typing indicator (3 bouncing dots)
 *   - Mobile responsive sidebar (overlay + drawer)
 */

// =========================================================================
//  SECTION 0: Theme Management (Day / Night)
// =========================================================================

var THEME_KEY = "steam_agent_theme";

function getTheme() {
    var stored = localStorage.getItem(THEME_KEY);
    return stored || "dark";
}

function setTheme(theme) {
    theme = theme || "dark";
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);

    // Update toggle button active states
    var lightBtn = document.getElementById("theme-light-btn");
    var darkBtn = document.getElementById("theme-dark-btn");
    if (lightBtn && darkBtn) {
        if (theme === "light") {
            lightBtn.classList.add("active");
            darkBtn.classList.remove("active");
        } else {
            darkBtn.classList.add("active");
            lightBtn.classList.remove("active");
        }
    }
}

function initTheme() {
    setTheme(getTheme());
}

// =========================================================================
//  SECTION 1: Session Management
// =========================================================================

function generateSessionId() {
    const stored = localStorage.getItem("steam_agent_session_id");
    if (stored) return stored;
    const id = "sess_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("steam_agent_session_id", id);
    return id;
}

const SESSION_ID = generateSessionId();

// =========================================================================
//  SECTION 2: DOM References
// =========================================================================

const chatContainer = document.getElementById("chat-container");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const quickPromptsContainer = document.getElementById("quick-prompts");

// Desktop sidebar elements
const syncBtn = document.getElementById("sync-btn");
const syncIcon = document.getElementById("sync-icon");
const syncText = document.getElementById("sync-text");
const dataStatus = document.getElementById("data-status");
const prefStatus = document.getElementById("pref-status");
const confidenceStatus = document.getElementById("confidence-status");
const agentThought = document.getElementById("agent-thought");

// Mobile sidebar elements
const mobileMenuBtn = document.getElementById("mobile-menu-btn");
const mobileSyncBtn = document.getElementById("mobile-sync-btn");
const mobileSyncIcon = document.getElementById("mobile-sync-icon");
const mobileSyncText = document.getElementById("mobile-sync-text");

// =========================================================================
//  SECTION 3: Quick Prompt Chips (randomised pool)
// =========================================================================

const QUICK_PROMPT_POOL = [
    { icon: "🔍", text: "基于我的游戏库推荐",     message: "分析我的游戏偏好，基于我常玩的类型推荐几款新游戏" },
    { icon: "💰", text: "100元以内的 RPG",        message: "推荐几款 100 元以内的高评价 RPG 游戏" },
    { icon: "🎲", text: "随机惊喜独立游戏",        message: "随便给我推荐一个让人惊艳的独立游戏，像星露谷那样的" },
    { icon: "🎯", text: "冷门神作挖掘",           message: "有没有什么冷门但评价超高、容易被错过的游戏？" },
    { icon: "👥", text: "双人联机推荐",           message: "想找能和朋友一起玩的合作或联机游戏，预算不限" },
    { icon: "😱", text: "恐怖游戏推荐",           message: "推荐几款真正吓人的恐怖游戏，氛围感要强" },
    { icon: "🏰", text: "开放世界探索",           message: "推荐一些能自由探索的开放世界游戏，画面要美的" },
    { icon: "⚔️", text: "魂类硬核挑战",           message: "想找几款高难度的魂类游戏，或者类似的硬核动作游戏" },
    { icon: "🌾", text: "治愈种田模拟",           message: "推荐一些轻松治愈的种田或模拟经营游戏" },
    { icon: "🚀", text: "科幻太空题材",           message: "有没有好的科幻题材游戏推荐？太空探索或者赛博朋克都行" },
    { icon: "🎮", text: "手柄体验最佳",           message: "哪些游戏用手柄玩体验特别好？接电视玩的那种" },
    { icon: "💸", text: "50元以内的神作",         message: "预算只有50块，推荐几款性价比超高的好游戏" },
    { icon: "🏎️", text: "竞速飞车推荐",           message: "推荐几款刺激的赛车竞速游戏，画面要好的" },
    { icon: "🧩", text: "烧脑解谜游戏",           message: "有没有逻辑性很强、需要动脑的解谜游戏推荐？" },
    { icon: "🎵", text: "音乐节奏游戏",           message: "推荐几款节奏感强的音乐游戏或音游" },
    { icon: "🗡️", text: "动作刷刷刷",            message: "想找刷刷刷的爽快动作游戏，类似暗黑破坏神那种" },
    { icon: "🏹", text: "生存建造游戏",           message: "推荐一些生存建造类的游戏，可以盖房子探索世界" },
    { icon: "📜", text: "剧情向叙事游戏",         message: "有没有剧情特别出色的游戏推荐？像电影一样的叙事体验" },
    { icon: "🕹️", text: "像素复古风",             message: "推荐几款像素画风但玩法出色的复古风格游戏" },
    { icon: "🐉", text: "日式JRPG精品",           message: "有哪些值得一玩的日式JRPG？剧情和战斗系统要好的" },
];

/**
 * Fisher-Yates shuffle (in-place), returns the array for chaining.
 */
function shuffleArray(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
        var j = Math.floor(Math.random() * (i + 1));
        var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
    }
    return arr;
}

function renderQuickPrompts() {
    if (!quickPromptsContainer) return;
    quickPromptsContainer.innerHTML = "";

    // Randomly pick 5 prompts from the pool
    var selected = shuffleArray(QUICK_PROMPT_POOL.slice()).slice(0, 5);

    selected.forEach(function (p) {
        var btn = document.createElement("button");
        btn.className = "whitespace-nowrap bg-steam-panel border border-steam-light/60 text-xs text-gray-300 px-4 py-2 rounded-full hover:border-steam-accent hover:text-steam-accent hover:bg-steam-dark transition-all shadow-sm flex-shrink-0";
        btn.textContent = p.icon + " " + p.text;
        btn.addEventListener("click", function () {
            chatInput.value = p.message;
            handleSend();
        });
        quickPromptsContainer.appendChild(btn);
    });
}

// =========================================================================
//  SECTION 4: Utility Helpers
// =========================================================================

function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function scrollToBottom() {
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

/** Render simple markdown: **bold** -> <strong>, newlines -> <br> */
function renderMarkdown(text) {
    var html = escapeHtml(text);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong class="text-steam-accent">$1</strong>');
    html = html.replace(/\n/g, "<br>");
    return html;
}

// =========================================================================
//  SECTION 5: Message Bubbles
// =========================================================================

/** User message — right-aligned, avatar on right, label "你" */
function appendUserMessage(text) {
    var wrapper = document.createElement("div");
    wrapper.className = "flex gap-4 flex-row-reverse";
    wrapper.style.animation = "fadeIn 0.3s ease-out";

    wrapper.innerHTML =
        '<div class="w-10 h-10 rounded-full bg-steam-light flex items-center justify-center flex-shrink-0 border border-steam-accent/50 overflow-hidden">' +
        '  <img src="https://api.dicebear.com/7.x/adventurer/svg?seed=Alex&backgroundColor=2a475e" alt="avatar" class="w-full h-full object-cover">' +
        '</div>' +
        '<div class="max-w-[85%] md:max-w-[70%]">' +
        '  <p class="text-xs text-gray-400 mb-1.5 mr-1 font-medium text-right">你</p>' +
        '  <div class="bg-[#1a2d3d] p-4 rounded-2xl rounded-tr-none text-white text-[15px] shadow-md border border-steam-accent/20">' +
             escapeHtml(text) +
        '  </div>' +
        '</div>';

    chatContainer.appendChild(wrapper);
    scrollToBottom();
}

/**
 * AI text-only message — left-aligned, bot icon, label "Steam 智能品鉴师".
 * Returns the wrapper element so callers can append cards later.
 */
function appendAITextMessage(text) {
    var html = renderMarkdown(text);

    var wrapper = document.createElement("div");
    wrapper.className = "flex gap-4";
    wrapper.style.animation = "fadeIn 0.3s ease-out";

    wrapper.innerHTML =
        '<div class="w-10 h-10 rounded-full bg-gradient-to-br from-steam-accent to-blue-600 flex items-center justify-center flex-shrink-0 shadow-[0_0_10px_rgba(102,192,244,0.3)]">' +
        '  <i class="fa-solid fa-robot text-white text-sm"></i>' +
        '</div>' +
        '<div class="max-w-[85%] md:max-w-[70%]">' +
        '  <p class="text-xs text-gray-400 mb-1.5 ml-1 font-medium">Steam 智能品鉴师</p>' +
        '  <div class="bg-steam-panel p-4 rounded-2xl rounded-tl-none border border-steam-light shadow-lg text-[15px] text-gray-200 ai-text-content">' +
             html +
        '  </div>' +
        '</div>';

    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

// =========================================================================
//  SECTION 6: Typing Indicator
// =========================================================================

function showTypingIndicator() {
    var wrapper = document.createElement("div");
    wrapper.className = "flex gap-4";
    wrapper.style.animation = "fadeIn 0.3s ease-out";
    wrapper.id = "typing-indicator";

    wrapper.innerHTML =
        '<div class="w-10 h-10 rounded-full bg-gradient-to-br from-steam-accent to-blue-600 flex items-center justify-center flex-shrink-0">' +
        '  <i class="fa-solid fa-robot text-white text-sm"></i>' +
        '</div>' +
        '<div class="flex flex-col gap-1.5">' +
        '  <div class="bg-steam-panel px-5 py-3 rounded-2xl rounded-tl-none border border-steam-light flex items-center gap-1.5">' +
        '    <div class="w-2 h-2 bg-steam-accent rounded-full typing-dot"></div>' +
        '    <div class="w-2 h-2 bg-steam-accent rounded-full typing-dot"></div>' +
        '    <div class="w-2 h-2 bg-steam-accent rounded-full typing-dot"></div>' +
        '  </div>' +
        '  <p id="typing-progress" class="text-xs text-gray-500 ml-1 hidden"></p>' +
        '</div>';

    chatContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function removeTypingIndicator() {
    var el = document.getElementById("typing-indicator");
    if (el) el.remove();
}

// =========================================================================
//  SECTION 7: Game Card Rendering
// =========================================================================

/**
 * Try to match game names from tool results against the AI response text.
 * Only returns games that are actually discussed in the text, so the cards
 * always correlate with what the user is reading.
 *
 * Fallback: if no matches found, return all games (the LLM might have
 * paraphrased the names).
 */
function filterGamesByMention(games, aiText) {
    if (!games || games.length === 0) return games;
    if (!aiText) return games;

    var textLower = aiText.toLowerCase();
    var mentioned = [];

    for (var i = 0; i < games.length; i++) {
        var name = (games[i].name || "").toLowerCase();
        if (!name) { mentioned.push(games[i]); continue; }

        // Direct substring match — most reliable
        if (textLower.indexOf(name) !== -1) {
            mentioned.push(games[i]);
            continue;
        }

        // Split into significant words (filter out short/common words)
        var words = name.split(/[\s:：\-—–—()（）\/]+/).filter(function (w) {
            return w.length > 2;
        });

        var matchCount = 0;
        for (var j = 0; j < words.length; j++) {
            if (textLower.indexOf(words[j]) !== -1) matchCount++;
        }

        // Require > 50% of significant words to match
        if (words.length > 0 && matchCount > words.length * 0.5) {
            mentioned.push(games[i]);
        }
    }

    // If nothing matched, fall back to showing all — the LLM may have
    // used translated or abbreviated names.
    return mentioned.length > 0 ? mentioned : games;
}

/**
 * Build a single game card from structured data returned by the backend.
 *
 * Expected fields on `game`:
 *   name, price, review (0.0-1.0), tags (string[]),
 *   description, header_image, store_url
 */
function renderGameCard(game) {
    var price = game.price || 0;
    var reviewPct = game.review != null ? Math.round(game.review * 100) : null;
    var tags = game.tags || [];
    var description = game.description || "";
    var imgSrc = game.header_image || "";

    // --- review badge ---
    var reviewBadgeHtml = "";
    if (reviewPct !== null) {
        var badgeColor, badgeText;
        if (reviewPct >= 95) {
            badgeColor = "bg-yellow-500/90";
            badgeText = "好评如潮 (" + reviewPct + "%)";
        } else if (reviewPct >= 80) {
            badgeColor = "bg-green-500/90";
            badgeText = "特别好评 (" + reviewPct + "%)";
        } else if (reviewPct >= 70) {
            badgeColor = "bg-blue-500/90";
            badgeText = "多半好评 (" + reviewPct + "%)";
        } else {
            badgeColor = "bg-gray-500/90";
            badgeText = "评价 " + reviewPct + "%";
        }
        reviewBadgeHtml =
            '<div class="absolute top-2 right-2 ' + badgeColor +
            ' backdrop-blur-sm text-white text-xs font-bold px-2.5 py-1 rounded shadow">' +
            badgeText + '</div>';
    }

    // --- tags ---
    var tagsHtml = tags.slice(0, 4).map(function (t) {
        return '<span class="bg-[#2a475e]/80 border border-[#66c0f4]/30 text-[#66c0f4] text-[11px] px-2 py-0.5 rounded-sm">' +
               escapeHtml(t) + '</span>';
    }).join("");

    // --- price ---
    var priceHtml;
    if (price === 0) {
        priceHtml = '<span class="text-steam-accent font-bold text-lg leading-none">免费</span>';
    } else {
        priceHtml = '<span class="text-steam-accent font-bold text-lg leading-none">¥' + price + '</span>';
    }

    // --- image ---
    var imageHtml;
    if (imgSrc) {
        imageHtml =
            '<img src="' + escapeHtml(imgSrc) +
            '" class="object-cover w-full h-full opacity-80 group-hover:opacity-100 group-hover:scale-105 transition-all duration-500"' +
            ' alt="' + escapeHtml(game.name) + '" loading="lazy">';
    } else {
        imageHtml =
            '<div class="flex items-center justify-center h-full text-gray-500">' +
            '<i class="fa-solid fa-image text-2xl"></i></div>';
    }

    // --- store link ---
    var storeHtml;
    if (game.store_url) {
        storeHtml =
            '<a href="' + escapeHtml(game.store_url) +
            '" target="_blank" rel="noopener" class="bg-steam-light hover:bg-steam-hover text-white text-xs px-3 py-1.5 rounded transition shadow-md"' +
            ' onclick="event.stopPropagation()">商店页面 <i class="fa-solid fa-arrow-up-right-from-square ml-1"></i></a>';
    } else {
        storeHtml =
            '<button class="bg-steam-light hover:bg-steam-hover text-white text-xs px-3 py-1.5 rounded transition shadow-md"' +
            ' onclick="event.stopPropagation(); window.open(\'https://store.steampowered.com/search/?term=' +
            encodeURIComponent(game.name) + '\', \'_blank\')">商店页面 <i class="fa-solid fa-arrow-up-right-from-square ml-1"></i></button>';
    }

    var card = document.createElement("div");
    card.className =
        "bg-steam-dark rounded-xl border border-steam-light/60 hover:border-steam-accent/80 " +
        "transition-all duration-300 flex flex-col overflow-hidden group shadow-lg " +
        "hover:shadow-[0_0_20px_rgba(102,192,244,0.15)] cursor-pointer";

    card.innerHTML =
        '<div class="h-36 bg-gray-800 relative overflow-hidden">' +
            imageHtml +
            reviewBadgeHtml +
            '<div class="absolute bottom-0 left-0 w-full h-1/2 bg-gradient-to-t from-steam-dark to-transparent"></div>' +
        '</div>' +
        '<div class="p-4 flex-1 flex flex-col justify-between relative z-10 -mt-4">' +
            '<div>' +
                '<h4 class="text-white font-bold text-lg mb-2 drop-shadow-md">' + escapeHtml(game.name) + '</h4>' +
                (tagsHtml ? '<div class="flex flex-wrap gap-1.5 mb-3">' + tagsHtml + '</div>' : "") +
                (description ? '<p class="text-xs text-gray-400 line-clamp-2 mb-3">' + escapeHtml(description.substring(0, 120)) + '</p>' : "") +
            '</div>' +
            '<div class="flex justify-between items-end mt-2 pt-3 border-t border-gray-700/50">' +
                '<div class="flex items-center gap-2">' + priceHtml + '</div>' +
                storeHtml +
            '</div>' +
        '</div>';

    // Handle image load errors: replace broken image with fallback icon
    var img = card.querySelector("img");
    if (img) {
        img.addEventListener("error", function () {
            var fallback = document.createElement("div");
            fallback.className = "flex items-center justify-center h-full text-gray-500";
            fallback.innerHTML = '<i class="fa-solid fa-image text-2xl"></i>';
            img.parentElement.appendChild(fallback);
            img.remove();
        });
    }

    return card;
}

/**
 * Append game cards into an existing AI message bubble.
 */
function appendGameCardsToMessage(messageWrapper, games) {
    if (!games || games.length === 0) return;

    var cardsGrid = document.createElement("div");
    cardsGrid.className = "grid grid-cols-1 lg:grid-cols-2 gap-5 mt-4";

    games.forEach(function (game) {
        cardsGrid.appendChild(renderGameCard(game));
    });

    // Append into the .ai-text-content div inside the wrapper
    var textContent = messageWrapper.querySelector(".ai-text-content");
    if (textContent) {
        textContent.appendChild(cardsGrid);
    }
    scrollToBottom();
}

// =========================================================================
//  SECTION 8: SSE Streaming + Chat Core
// =========================================================================

var isStreaming = false;
var agentStatusReader = null;  // ReadableStreamDefaultReader for SSE subscription

// =========================================================================
//  Agent Status SSE Subscription (real‑time push, replaces polling)
// =========================================================================

/** Apply a status update object to the sidebar DOM (desktop + mobile). */
function applyStatusUpdate(data) {
    if (dataStatus && data.data_source) {
        dataStatus.textContent = data.data_source;
        if (data.data_source.indexOf("Steam") !== -1) {
            dataStatus.classList.remove("bg-gray-800");
            dataStatus.classList.add("bg-green-600/20", "text-green-400");
        }
    }
    if (confidenceStatus && data.confidence != null) {
        confidenceStatus.textContent = data.confidence + "%";
    }
    if (agentThought && data.agent_thought) {
        agentThought.innerHTML = '"' + escapeHtml(data.agent_thought) + '"';
        var mt = document.getElementById("mobile-agent-thought");
        if (mt) mt.innerHTML = '"' + escapeHtml(data.agent_thought) + '"';
    }
    if (prefStatus && data.preference_bias != null) {
        prefStatus.textContent = data.preference_bias;
        var mp = document.getElementById("mobile-pref-status");
        if (mp) mp.textContent = data.preference_bias;
    }
    var mds = document.getElementById("mobile-data-status");
    if (mds && data.data_source) {
        mds.textContent = data.data_source;
        if (data.data_source.indexOf("Steam") !== -1) {
            mds.classList.remove("bg-gray-800");
            mds.classList.add("bg-green-600/20", "text-green-400");
        }
    }
    var mc = document.getElementById("mobile-confidence-status");
    if (mc && data.confidence != null) {
        mc.textContent = data.confidence + "%";
    }
}

function startAgentStatusSubscription() {
    if (agentStatusReader) return;  // already subscribed

    var connect = function () {
        fetch("/api/agent-stream/" + SESSION_ID)
            .then(function (response) {
                if (!response.ok) {
                    // Fallback: retry after delay
                    setTimeout(connect, 5000);
                    return;
                }
                var reader = response.body.getReader();
                agentStatusReader = reader;
                var decoder = new TextDecoder();
                var buffer = "";
                var currentEvent = "";

                function readLoop() {
                    reader.read().then(function (result) {
                        if (result.done) {
                            agentStatusReader = null;
                            // Reconnect after a short delay
                            setTimeout(connect, 2000);
                            return;
                        }

                        buffer += decoder.decode(result.value, { stream: true });
                        var lines = buffer.split("\n");
                        buffer = lines.pop();

                        for (var i = 0; i < lines.length; i++) {
                            var line = lines[i];
                            if (line.indexOf("event: ") === 0) {
                                currentEvent = line.slice(7).trim();
                            } else if (line.indexOf("data: ") === 0) {
                                if (currentEvent === "status" || !currentEvent) {
                                    try {
                                        var data = JSON.parse(line.slice(6));
                                        applyStatusUpdate(data);
                                    } catch (e) {}
                                }
                                currentEvent = "";
                            }
                            // Heartbeat comments (lines starting with ":") are ignored
                        }

                        readLoop();  // continue reading
                    }).catch(function () {
                        agentStatusReader = null;
                        setTimeout(connect, 5000);  // reconnect on error
                    });
                }
                readLoop();
            })
            .catch(function () {
                agentStatusReader = null;
                setTimeout(connect, 5000);
            });
    };

    connect();
}

function stopAgentStatusSubscription() {
    if (agentStatusReader) {
        try { agentStatusReader.cancel(); } catch (e) {}
        agentStatusReader = null;
    }
}

async function handleSend() {
    if (isStreaming) return;

    var text = chatInput.value.trim();
    if (!text) return;
    isStreaming = true;

    // --- Disable input ---
    chatInput.value = "";
    chatInput.disabled = true;
    sendBtn.disabled = true;
    sendBtn.classList.add("opacity-50", "cursor-not-allowed");

    // --- Show user message ---
    appendUserMessage(text);

    // --- Show typing indicator ---
    showTypingIndicator();

    var fullText = "";
    var aiMessageWrapper = null;

    try {
        var response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: SESSION_ID, message: text, settings: loadSettings() }),
        });

        if (!response.ok) {
            removeTypingIndicator();
            appendAITextMessage("❌ 请求失败 (" + response.status + ")");
            return;
        }

        // --- Read SSE stream ---
        // Keep the typing indicator visible while the backend pre‑processes
        // (profile extraction, tool‑chain game search, prompt construction).
        // Only remove it when the first real content token arrives.
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        var lastPaint = 0;
        var PAINT_INTERVAL_MS = 40;  // update DOM at most every 40 ms
        var ended = false;
        var firstToken = true;
        var progressEl = document.getElementById("typing-progress");

        while (!ended) {
            var readResult = await reader.read();

            if (readResult.value) {
                buffer += decoder.decode(readResult.value, { stream: true });
            }

            var lines = buffer.split("\n");
            buffer = lines.pop(); // keep incomplete line

            for (var i = 0; i < lines.length; i++) {
                var line = lines[i];
                if (line.indexOf("data: ") !== 0) continue;
                var payload = line.slice(6);

                if (payload === "[DONE]") {
                    ended = true;
                    break;
                }

                // --- Handle progress messages from the backend ---
                // Backend yields "__PROGRESS__<message>" while it pre‑processes
                // so the SSE stream has data flowing immediately.
                if (payload.indexOf("__PROGRESS__") === 0) {
                    var progressMsg = payload.slice(12);
                    if (progressEl) {
                        progressEl.textContent = progressMsg;
                        progressEl.classList.remove("hidden");
                    }
                    continue;  // don't add to fullText or render as content
                }

                // --- First real token → swap typing indicator for AI bubble ---
                if (firstToken) {
                    removeTypingIndicator();
                    aiMessageWrapper = appendAITextMessage("");
                    var textContent = aiMessageWrapper.querySelector(".ai-text-content");
                    progressEl = null;  // old progress element is gone
                    firstToken = false;
                }

                var token = payload.replace(/\\n/g, "\n");
                fullText += token;

                // Throttle: only touch the DOM every PAINT_INTERVAL_MS so the
                // browser has time to repaint between batches.
                var now = Date.now();
                if (now - lastPaint >= PAINT_INTERVAL_MS) {
                    textContent.innerHTML = renderMarkdown(fullText);
                    scrollToBottom();
                    lastPaint = now;
                    // Explicitly yield to the event loop so the browser can paint
                    await new Promise(function (r) { setTimeout(r, 0); });
                }
            }

            if (readResult.done) {
                ended = true;
            }
        }

        // Final render — always show the complete text
        if (!firstToken) {
            textContent.innerHTML = renderMarkdown(fullText);
            scrollToBottom();
        } else {
            // Stream ended with only progress messages (no real content) —
            // e.g. backend error was caught.  Clean up and show fallback.
            removeTypingIndicator();
            aiMessageWrapper = appendAITextMessage("抱歉，未能生成回复。请再试一次。");
        }

        // ================================================================
        //  AFTER STREAM: fetch structured game data & filter by mention
        // ================================================================
        try {
            var resultsRes = await fetch("/api/tool-results/" + SESSION_ID);
            if (resultsRes.ok) {
                var data = await resultsRes.json();
                var allGames = data.games || [];
                // Only show cards for games the AI actually talked about
                var relevantGames = filterGamesByMention(allGames, fullText);
                if (relevantGames.length > 0 && aiMessageWrapper) {
                    appendGameCardsToMessage(aiMessageWrapper, relevantGames);
                }
            }
        } catch (err) {
            console.warn("Failed to fetch tool results:", err);
        }

    } catch (err) {
        removeTypingIndicator();
        appendAITextMessage("❌ 连接错误：" + escapeHtml(err.message));
    } finally {
        chatInput.disabled = false;
        sendBtn.disabled = false;
        sendBtn.classList.remove("opacity-50", "cursor-not-allowed");
        chatInput.focus();
        scrollToBottom();
        isStreaming = false;
    }
}

// =========================================================================
//  SECTION 9: Authentication (login / register / logout)
// =========================================================================

var AUTH_TOKEN_KEY = "steam_agent_auth_token";
var authToken = localStorage.getItem(AUTH_TOKEN_KEY) || "";
var currentUser = null;  // {id, username, avatar_seed, ...}
var authMode = "login";  // "login" | "register"

function isLoggedIn() {
    return !!authToken && !!currentUser;
}

/** Update sidebar UI after login/logout. */
function applyAuthUI(user) {
    var userName = document.getElementById("user-name");
    var avatar = document.getElementById("user-avatar");
    var onlineStatus = document.getElementById("online-status");
    var desktopLoginBtn = document.getElementById("desktop-login-btn");
    var desktopLogoutBtn = document.getElementById("desktop-logout-btn");
    var mobileLoginBtn = document.getElementById("mobile-login-btn");
    var mobileLogoutBtn = document.getElementById("mobile-logout-btn");

    if (user) {
        // Logged in
        if (userName) userName.textContent = user.username;
        if (avatar) avatar.src = "https://api.dicebear.com/7.x/adventurer/svg?seed=" +
                                  encodeURIComponent(user.avatar_seed || user.username) +
                                  "&backgroundColor=2a475e";
        if (onlineStatus) onlineStatus.textContent = "已登录";

        // Show logout, hide login
        if (desktopLoginBtn) desktopLoginBtn.classList.add("hidden");
        if (desktopLogoutBtn) desktopLogoutBtn.classList.remove("hidden");
        if (mobileLoginBtn) mobileLoginBtn.classList.add("hidden");
        if (mobileLogoutBtn) mobileLogoutBtn.classList.remove("hidden");

        // Update mobile sidebar avatars/names
        var mobileAvatars = document.querySelectorAll("#mobile-sidebar img[alt='avatar']");
        mobileAvatars.forEach(function (img) {
            img.src = "https://api.dicebear.com/7.x/adventurer/svg?seed=" +
                      encodeURIComponent(user.avatar_seed || user.username) +
                      "&backgroundColor=2a475e";
        });
        var mobileNames = document.querySelectorAll("#mobile-sidebar p.text-white.font-bold");
        mobileNames.forEach(function (p) { p.textContent = user.username; });
    } else {
        // Logged out — reset to defaults
        if (userName) userName.textContent = "Player_One";
        if (avatar) avatar.src = "https://api.dicebear.com/7.x/adventurer/svg?seed=Alex&backgroundColor=2a475e";
        if (onlineStatus) onlineStatus.textContent = "在线并已准备好";

        // Show login, hide logout
        if (desktopLoginBtn) desktopLoginBtn.classList.remove("hidden");
        if (desktopLogoutBtn) desktopLogoutBtn.classList.add("hidden");
        if (mobileLoginBtn) mobileLoginBtn.classList.remove("hidden");
        if (mobileLogoutBtn) mobileLogoutBtn.classList.add("hidden");
    }
}

/**
 * Call /api/user/link to restore all user data after login / page-load.
 * Then update the UI: chat history, Steam data, preferences, settings.
 */
async function restoreUserState(token) {
    if (!token) return;
    try {
        var res = await fetch("/api/user/link", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: SESSION_ID, token: token }),
        });
        var data = await res.json();
        if (!data.success) return;

        // 1. Restore past conversations into the chat UI
        if (data.conversations && data.conversations.length > 0) {
            restoreConversationsToUI(data.conversations);
        }

        // 2. Restore Steam profile → sidebar
        if (data.steam_profile && data.steam_profile.success) {
            isSteamConnected = true;
            isSteamSimulated = false;
            updateSidebarStatus(data.steam_profile);
            // Restore Steam ID
            if (data.steam_profile.steam_id) {
                savedSteamId = data.steam_profile.steam_id;
                localStorage.setItem("steam_agent_steam_id", savedSteamId);
            }
            // Update sync button
            setSyncUI(syncBtn, syncIcon, syncText, "bg-green-600", "fa-check", false,
                      "已接入 Steam 库");
            setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-green-600", "fa-check", false,
                      "已接入 Steam 库");
        }

        // 3. Restore settings → localStorage + sidebar
        if (data.settings) {
            saveSettingsToStorage(data.settings);
            updateSidebarPrefDisplay(data.settings);
            updatePrefsDisplaySection(data.settings);
        }
    } catch (err) {
        console.warn("Failed to restore user state:", err);
    }
}

/**
 * Render past conversations in the chat UI.
 * Called after login restores history from the server.
 */
function restoreConversationsToUI(conversations) {
    if (!conversations || conversations.length === 0) return;
    // Clear any existing welcome message first (the chat container may have
    // the default empty-state message). We keep it simple: just append history.
    for (var i = 0; i < conversations.length; i++) {
        var msg = conversations[i];
        if (msg.role === "user") {
            appendUserMessage(msg.content);
        } else if (msg.role === "assistant") {
            appendAITextMessage(msg.content);
        }
    }
}

function openAuthModal(mode) {
    mode = mode || "login";
    authMode = mode;
    var modal = document.getElementById("auth-modal");
    if (!modal) return;
    var title = document.getElementById("auth-modal-title");
    var submitBtn = document.getElementById("auth-submit-btn");
    var toggleText = document.getElementById("auth-toggle-text");
    var toggleBtn = document.getElementById("auth-toggle-btn");
    var errorEl = document.getElementById("auth-error");
    var usernameInput = document.getElementById("auth-username");
    var passwordInput = document.getElementById("auth-password");

    if (errorEl) errorEl.classList.add("hidden");
    if (usernameInput) usernameInput.value = "";
    if (passwordInput) passwordInput.value = "";

    if (mode === "login") {
        if (title) title.innerHTML = '<i class="fa-solid fa-right-to-bracket text-steam-accent"></i> 登录';
        if (submitBtn) submitBtn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> 登录';
        if (toggleText) toggleText.textContent = "还没有账号？";
        if (toggleBtn) toggleBtn.textContent = "立即注册";
    } else {
        if (title) title.innerHTML = '<i class="fa-solid fa-user-plus text-steam-accent"></i> 注册';
        if (submitBtn) submitBtn.innerHTML = '<i class="fa-solid fa-user-plus"></i> 注册';
        if (toggleText) toggleText.textContent = "已有账号？";
        if (toggleBtn) toggleBtn.textContent = "去登录";
    }

    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    if (usernameInput) setTimeout(function () { usernameInput.focus(); }, 100);
}

function closeAuthModal() {
    var modal = document.getElementById("auth-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

async function handleAuthSubmit() {
    var usernameInput = document.getElementById("auth-username");
    var passwordInput = document.getElementById("auth-password");
    var errorEl = document.getElementById("auth-error");
    var submitBtn = document.getElementById("auth-submit-btn");

    var username = usernameInput ? usernameInput.value.trim() : "";
    var password = passwordInput ? passwordInput.value : "";

    if (!username || !password) {
        if (errorEl) { errorEl.textContent = "请填写用户名和密码"; errorEl.classList.remove("hidden"); }
        return;
    }
    if (username.length < 2) {
        if (errorEl) { errorEl.textContent = "用户名至少需要 2 个字符"; errorEl.classList.remove("hidden"); }
        return;
    }
    if (password.length < 4) {
        if (errorEl) { errorEl.textContent = "密码至少需要 4 个字符"; errorEl.classList.remove("hidden"); }
        return;
    }

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.classList.add("opacity-50", "cursor-not-allowed");
    }

    try {
        var endpoint = authMode === "login" ? "/api/auth/login" : "/api/auth/register";
        var res = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: username, password: password }),
        });
        var data = await res.json();

        if (data.success) {
            authToken = data.token;
            currentUser = data.user;
            localStorage.setItem(AUTH_TOKEN_KEY, authToken);
            closeAuthModal();
            applyAuthUI(currentUser);
            showToast((authMode === "login" ? "欢迎回来，" : "注册成功，") + currentUser.username + "！", "success");

            // RESTORE all user data from server (conversations, Steam, preferences, settings)
            restoreUserState(authToken);

            // Also sync any local settings to the server
            var savedSettings = loadSettings();
            if (savedSettings.genres.length > 0 || savedSettings.budget || savedSettings.platforms.length > 0) {
                updateSidebarPrefDisplay(savedSettings);
                updatePrefsDisplaySection(savedSettings);
                // Push to server
                syncSettingsToServer(savedSettings);
            }
        } else {
            if (errorEl) { errorEl.textContent = data.error || "操作失败"; errorEl.classList.remove("hidden"); }
        }
    } catch (err) {
        if (errorEl) { errorEl.textContent = "网络错误：" + err.message; errorEl.classList.remove("hidden"); }
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove("opacity-50", "cursor-not-allowed");
        }
    }
}

/** Validate stored token on page load. */
async function validateAuthToken() {
    if (!authToken) return;
    try {
        var res = await fetch("/api/auth/me?token=" + encodeURIComponent(authToken));
        var data = await res.json();
        if (data.success) {
            currentUser = data.user;
            applyAuthUI(currentUser);
            // Restore all user data from server (conversations, Steam, preferences)
            await restoreUserState(authToken);

            // Also restore settings from server if present
            if (data.user.settings_json) {
                try {
                    var serverSettings = JSON.parse(data.user.settings_json);
                    if (serverSettings) {
                        saveSettingsToStorage(serverSettings);
                        updateSidebarPrefDisplay(serverSettings);
                        updatePrefsDisplaySection(serverSettings);
                    }
                } catch (e) {}
            }
        } else {
            // Token expired or invalid
            authToken = "";
            currentUser = null;
            localStorage.removeItem(AUTH_TOKEN_KEY);
        }
    } catch (e) {
        // Server not available — keep token and try again later
    }
}

// =========================================================================
//  SECTION 10: Steam Sync (Sidebar Button — now with real Steam Web API)
// =========================================================================

var isSyncing = false;
var isSteamConnected = false;       // true ONLY when real Steam API data is loaded (not simulated)
var isSteamSimulated = false;       // true when simulated/demo data is loaded
var savedSteamId = localStorage.getItem("steam_agent_steam_id") || "";

/** Update sidebar status indicators (used by both desktop and mobile). */
function updateSidebarStatus(data) {
    var isReal = data.success && !data.is_simulated;

    // Data source badge
    function setDataBadge(el, text, isRealSteam) {
        if (!el) return;
        el.textContent = text;
        // Reset all state classes first
        el.classList.remove("bg-gray-800", "bg-green-600/20", "text-green-400", "bg-yellow-600/20", "text-yellow-400");
        if (isRealSteam) {
            el.classList.add("bg-green-600/20", "text-green-400");
        } else if (data.is_simulated) {
            el.classList.add("bg-yellow-600/20", "text-yellow-400");
        } else {
            el.classList.add("bg-gray-800");
        }
    }
    setDataBadge(dataStatus, isReal ? "已接入 Steam 库" : (data.is_simulated ? "模拟 Steam 数据" : "本地数据库"), isReal);
    setDataBadge(document.getElementById("mobile-data-status"), isReal ? "已接入 Steam 库" : (data.is_simulated ? "模拟 Steam 数据" : "本地数据库"), isReal);

    // Top genres → preference bias
    if (data.top_genres && data.top_genres.length > 0) {
        var genreText = data.top_genres.slice(0, 2).join(" / ");
        if (data.is_simulated) genreText = "⚠️ " + genreText + " (模拟)";
        if (prefStatus) prefStatus.textContent = genreText;
        var mobilePref = document.getElementById("mobile-pref-status");
        if (mobilePref) mobilePref.textContent = genreText;
    }

    // Playtime analysis → agent thought
    if (data.recent_playtime_analysis) {
        var thoughtHtml = '"' + escapeHtml(data.recent_playtime_analysis) + '"';
        if (agentThought) agentThought.innerHTML = thoughtHtml;
        var mobileThought = document.getElementById("mobile-agent-thought");
        if (mobileThought) mobileThought.innerHTML = thoughtHtml;
    }

    // Confidence boost if real data (simulated data doesn't boost confidence)
    if (data.success && data.game_count > 0 && !data.is_simulated) {
        if (confidenceStatus) confidenceStatus.textContent = Math.min(95, 70 + Math.floor(data.game_count / 20)) + "%";
        var mobileConf = document.getElementById("mobile-confidence-status");
        if (mobileConf) mobileConf.textContent = Math.min(95, 70 + Math.floor(data.game_count / 20)) + "%";
    }

    // Update avatar and name if we have real profile data
    if (data.persona_name) {
        var userName = document.getElementById("user-name");
        if (userName) userName.textContent = data.persona_name;
    }
    if (data.avatar_url) {
        var avatar = document.getElementById("user-avatar");
        if (avatar) avatar.src = data.avatar_url;
        // Also update mobile sidebar avatar
        var mobileAvatars = document.querySelectorAll("#mobile-sidebar img[alt='avatar']");
        mobileAvatars.forEach(function (img) { img.src = data.avatar_url; });
    }
}

/** Helper to set sync button UI (both desktop and mobile). */
function setSyncUI(btn, icon, text, btnClass, iconClass, spin, label) {
    if (btn) {
        btn.classList.remove("bg-steam-light", "bg-gray-600", "bg-green-600", "bg-yellow-600");
        btn.classList.add(btnClass);
    }
    if (icon) {
        icon.classList.remove("fa-steam", "fa-spinner", "fa-check", "fa-exclamation-triangle", "fa-spin");
        icon.classList.add(iconClass);
        if (spin) icon.classList.add("fa-spin");
    }
    if (text) text.textContent = label;
}

/** Core sync function — can be called with or without a Steam ID. */
async function doSyncSteam(steamId) {
    isSyncing = true;

    setSyncUI(syncBtn, syncIcon, syncText, "bg-gray-600", "fa-spinner", true,
              steamId ? "正在读取 Steam API..." : "正在模拟同步...");
    setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-gray-600", "fa-spinner", true,
              steamId ? "正在读取 Steam API..." : "正在模拟同步...");

    try {
        var res = await fetch("/api/sync-steam", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: SESSION_ID, steam_id: steamId || "", token: authToken || "" }),
        });

        if (!res.ok) throw new Error("Sync failed");

        var data = await res.json();

        if (data.success && !data.is_simulated) {
            // ================================================================
            //  REAL Steam API data — green success
            // ================================================================
            setSyncUI(syncBtn, syncIcon, syncText, "bg-green-600", "fa-check", false,
                      data.message || ("同步完成 (库中 " + data.game_count + " 款游戏)"));
            setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-green-600", "fa-check", false,
                      data.message || ("同步完成 (库中 " + data.game_count + " 款游戏)"));

            isSteamConnected = true;
            isSteamSimulated = false;
            updateSidebarStatus(data);

            // Save Steam ID
            if (steamId) {
                localStorage.setItem("steam_agent_steam_id", steamId);
                savedSteamId = steamId;
            }

            // AI proactive message with real analysis
            setTimeout(function () {
                showTypingIndicator();
                setTimeout(function () {
                    removeTypingIndicator();
                    var msg;
                    if (data.persona_name && data.top_genres && data.top_genres.length > 0) {
                        msg = "已成功接入你的 Steam 账号 **" + escapeHtml(data.persona_name) +
                              "**！你的游戏库中有 **" + data.game_count +
                              " 款**游戏，偏好类型是 **" +
                              data.top_genres.slice(0, 2).join("、") +
                              "**。想让我基于你的真实游戏记录做推荐吗？直接告诉我你想玩什么类型的！";
                    } else {
                        msg = "我已经分析了你的 Steam 游戏库。**" + data.game_count +
                              " 款**游戏，偏好 **" +
                              (data.top_genres && data.top_genres.length > 0 ? data.top_genres.slice(0, 2).join("、") : "多种类型") +
                              "**。想找点什么？";
                    }
                    appendAITextMessage(msg);
                    scrollToBottom();
                }, 1200);
            }, 500);

        } else if (data.success && data.is_simulated) {
            // ================================================================
            //  SIMULATED data — yellow/orange warning, NOT green
            // ================================================================
            setSyncUI(syncBtn, syncIcon, syncText, "bg-yellow-600", "fa-exclamation-triangle", false,
                      "模拟数据 (142 款) — 点击接入真实 Steam");
            setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-yellow-600", "fa-exclamation-triangle", false,
                      "模拟数据 (142 款) — 点击接入真实 Steam");

            // CRITICAL: do NOT set isSteamConnected = true for simulated data
            isSteamConnected = false;
            isSteamSimulated = true;
            updateSidebarStatus(data);

            // AI proactive message — make it clear this is simulated
            setTimeout(function () {
                showTypingIndicator();
                setTimeout(function () {
                    removeTypingIndicator();
                    var msg = "⚠️ 当前使用的是**模拟数据**（142 款游戏，偏好 FPS/开放世界RPG 等）。" +
                              "如果你有 Steam 账号，点击侧边栏按钮**输入你的 Steam ID**，" +
                              "我就能分析你的真实游戏库，给你更精准的推荐！";
                    appendAITextMessage(msg);
                    scrollToBottom();
                }, 1200);
            }, 500);

        } else {
            // API returned success=false — reset button for retry
            setSyncUI(syncBtn, syncIcon, syncText, "bg-steam-light", "fa-steam", false, "接入 Steam 游戏库");
            setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-steam-light", "fa-steam", false, "接入 Steam 游戏库");
            isSteamConnected = false;
            isSteamSimulated = false;
            showToast("❌ " + (data.message || "同步失败"), "error");
        }
    } catch (err) {
        setSyncUI(syncBtn, syncIcon, syncText, "bg-steam-light", "fa-steam", false, "接入 Steam 游戏库");
        setSyncUI(mobileSyncBtn, mobileSyncIcon, mobileSyncText, "bg-steam-light", "fa-steam", false, "接入 Steam 游戏库");
        isSteamConnected = false;
        isSteamSimulated = false;
        console.error("Steam sync error:", err);
        showToast("❌ 网络错误，请稍后重试", "error");
    } finally {
        isSyncing = false;
    }
}

/** Click handler for sync buttons — opens the Steam ID modal. */
async function syncSteamData() {
    // Only prevent during active sync; allow re-sync after simulated data or real data
    if (isSyncing) return;

    // If already connected with real Steam data, ask for confirmation
    if (isSteamConnected) {
        // Re-clicking when already connected — allow re-syncing (maybe user changed Steam ID)
        // Just open the modal again
        openSteamIdModal();
        return;
    }

    // If we have simulated data, allow trying real sync
    // If we already have a saved Steam ID, sync directly
    if (savedSteamId) {
        await doSyncSteam(savedSteamId);
        return;
    }

    // Otherwise, show the modal to ask for Steam ID
    openSteamIdModal();
}

// =========================================================================
//  Steam ID Modal handlers
// =========================================================================

function openSteamIdModal() {
    var modal = document.getElementById("steam-id-modal");
    if (!modal) return;
    var input = document.getElementById("steam-id-input");
    if (input && savedSteamId) input.value = savedSteamId;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";

    // Check Steam API connectivity in the background
    var statusEl = document.getElementById("steam-api-status");
    if (statusEl) {
        statusEl.classList.remove("hidden");
        statusEl.className = "text-xs mb-4 p-2 rounded-lg bg-gray-800 text-gray-400";
        statusEl.textContent = "正在检查 Steam API 连通性...";
        fetch("/api/steam-check")
            .then(function (r) { return r.json(); })
            .then(function (s) {
                if (s.api_key_valid) {
                    statusEl.className = "text-xs mb-4 p-2 rounded-lg bg-green-600/20 text-green-400";
                    statusEl.innerHTML = '<i class="fa-solid fa-check-circle"></i> ' + s.detail;
                } else if (s.api_reachable && !s.api_key_valid) {
                    statusEl.className = "text-xs mb-4 p-2 rounded-lg bg-red-600/20 text-red-400";
                    statusEl.innerHTML = '<i class="fa-solid fa-times-circle"></i> ' + s.detail;
                } else {
                    statusEl.className = "text-xs mb-4 p-2 rounded-lg bg-yellow-600/20 text-yellow-400";
                    statusEl.innerHTML = '<i class="fa-solid fa-exclamation-triangle"></i> ' + s.detail;
                }
            })
            .catch(function () {
                statusEl.className = "text-xs mb-4 p-2 rounded-lg bg-yellow-600/20 text-yellow-400";
                statusEl.textContent = "无法检查 Steam API 状态，请确保服务已启动";
            });
    }
}

function closeSteamIdModal() {
    var modal = document.getElementById("steam-id-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

// =========================================================================
//  SECTION 10: Mobile Sidebar Toggle
// =========================================================================

function toggleMobileSidebar() {
    var sidebar = document.getElementById("mobile-sidebar");
    var overlay = document.getElementById("sidebar-overlay");

    if (sidebar.classList.contains("-translate-x-full")) {
        sidebar.classList.remove("-translate-x-full");
        sidebar.classList.add("translate-x-0");
        overlay.classList.remove("hidden");
    } else {
        sidebar.classList.add("-translate-x-full");
        sidebar.classList.remove("translate-x-0");
        overlay.classList.add("hidden");
    }
}

// =========================================================================
//  SECTION 11: Settings Modal
// =========================================================================

var SETTINGS_KEY = "steam_agent_settings";

function loadSettings() {
    try {
        var raw = localStorage.getItem(SETTINGS_KEY);
        return raw ? JSON.parse(raw) : { budget: null, genres: [], platforms: [] };
    } catch (e) {
        return { budget: null, genres: [], platforms: [] };
    }
}

function saveSettingsToStorage(settings) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

/** Apply saved settings to the chip UI */
function applySettingsToUI(settings) {
    // Budget chips
    document.querySelectorAll("#budget-options .budget-chip").forEach(function (chip) {
        var val = chip.getAttribute("data-value");
        if (settings.budget !== null && String(settings.budget) === val) {
            chip.classList.add("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        } else {
            chip.classList.remove("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        }
    });

    // Genre chips
    document.querySelectorAll("#genre-options .genre-chip").forEach(function (chip) {
        var val = chip.getAttribute("data-value");
        if (settings.genres.indexOf(val) !== -1) {
            chip.classList.add("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        } else {
            chip.classList.remove("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        }
    });

    // Platform chips
    document.querySelectorAll("#platform-options .platform-chip").forEach(function (chip) {
        var val = chip.getAttribute("data-value");
        if (settings.platforms.indexOf(val) !== -1) {
            chip.classList.add("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        } else {
            chip.classList.remove("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        }
    });
}

function openSettingsModal() {
    var modal = document.getElementById("settings-modal");
    if (!modal) return;
    var settings = loadSettings();
    applySettingsToUI(settings);
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
}

function closeSettingsModal() {
    var modal = document.getElementById("settings-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

function saveSettings() {
    var settings = { budget: null, genres: [], platforms: [] };

    // Read selected budget
    document.querySelectorAll("#budget-options .budget-chip").forEach(function (chip) {
        if (chip.classList.contains("border-steam-accent")) {
            var v = chip.getAttribute("data-value");
            settings.budget = v === "0" ? null : parseInt(v);
        }
    });

    // Read selected genres
    document.querySelectorAll("#genre-options .genre-chip").forEach(function (chip) {
        if (chip.classList.contains("border-steam-accent")) {
            settings.genres.push(chip.getAttribute("data-value"));
        }
    });

    // Read selected platforms
    document.querySelectorAll("#platform-options .platform-chip").forEach(function (chip) {
        if (chip.classList.contains("border-steam-accent")) {
            settings.platforms.push(chip.getAttribute("data-value"));
        }
    });

    saveSettingsToStorage(settings);
    closeSettingsModal();

    // Update sidebar preference displays
    updateSidebarPrefDisplay(settings);
    updatePrefsDisplaySection(settings);

    // Persist to server if logged in (so settings follow the user account)
    syncSettingsToServer(settings);

    showToast("偏好设置已保存 ✓", "success");
}

/**
 * Push local settings to the server so they persist across devices.
 */
async function syncSettingsToServer(settings) {
    if (!authToken) return;
    try {
        await fetch("/api/user/settings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: SESSION_ID,
                token: authToken,
                settings: settings,
            }),
        });
    } catch (e) {
        // Non-fatal — settings are still in localStorage
    }
}

/**
 * Update the "偏好倾向" line in the AI status panel (desktop + mobile).
 */
function updateSidebarPrefDisplay(settings) {
    var parts = [];
    if (settings.budget) parts.push("预算 ¥" + settings.budget);
    if (settings.genres.length > 0) parts.push(settings.genres.slice(0, 2).join("/"));
    if (settings.platforms.length > 0) parts.push(settings.platforms.join("/"));
    var prefText = parts.length > 0 ? parts.join(" · ") : "-";
    if (prefStatus) prefStatus.textContent = prefText;
    var mobilePref = document.getElementById("mobile-pref-status");
    if (mobilePref) mobilePref.textContent = prefText;
}

/**
 * Render the full "当前偏好" section in the sidebar (desktop + mobile).
 * Shows budget, genre chips, and platform chips with Steam styling.
 */
function updatePrefsDisplaySection(settings) {
    var hasAny = (settings.budget) || (settings.genres.length > 0) || (settings.platforms.length > 0);

    function updateOne(prefix, containerId, budgetId, genresId, platformsId) {
        var container = document.getElementById(containerId);
        if (!container) return;
        container.classList.toggle("hidden", !hasAny);

        // Budget
        var budgetEl = document.getElementById(budgetId);
        if (budgetEl) {
            budgetEl.classList.toggle("hidden", !settings.budget);
            var budgetSpan = budgetEl.querySelector("span:last-child");
            if (budgetSpan) budgetSpan.textContent = "¥" + settings.budget + " 以内";
        }

        // Genres
        var genresEl = document.getElementById(genresId);
        if (genresEl) {
            genresEl.classList.toggle("hidden", settings.genres.length === 0);
            var chipContainer = genresEl.querySelector("div");
            if (chipContainer) {
                chipContainer.innerHTML = settings.genres.map(function (g) {
                    return '<span class="bg-steam-accent/20 border border-steam-accent/40 text-steam-accent text-[10px] px-2 py-0.5 rounded-full">' + g + '</span>';
                }).join("");
            }
        }

        // Platforms
        var platformsEl = document.getElementById(platformsId);
        if (platformsEl) {
            platformsEl.classList.toggle("hidden", settings.platforms.length === 0);
            var platContainer = platformsEl.querySelector("div");
            if (platContainer) {
                platContainer.innerHTML = settings.platforms.map(function (p) {
                    return '<span class="bg-green-500/15 border border-green-500/40 text-green-400 text-[10px] px-2 py-0.5 rounded-full">' + p + '</span>';
                }).join("");
            }
        }
    }

    updateOne("desktop", "desktop-prefs-display", "desktop-pref-budget", "desktop-pref-genres", "desktop-pref-platforms");
    updateOne("mobile",  "mobile-prefs-display",  "mobile-pref-budget",  "mobile-pref-genres",  "mobile-pref-platforms");
}

// Chip click handlers (uses event delegation because chips are inside the modal)
document.addEventListener("click", function (e) {
    var chip = e.target.closest(".budget-chip");
    if (chip) {
        e.preventDefault();
        // Budget is single-select
        document.querySelectorAll("#budget-options .budget-chip").forEach(function (c) {
            c.classList.remove("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        });
        chip.classList.add("bg-steam-accent/20", "border-steam-accent", "text-steam-accent");
        return;
    }

    chip = e.target.closest(".genre-chip");
    if (chip) {
        e.preventDefault();
        chip.classList.toggle("bg-steam-accent/20");
        chip.classList.toggle("border-steam-accent");
        chip.classList.toggle("text-steam-accent");
        return;
    }

    chip = e.target.closest(".platform-chip");
    if (chip) {
        e.preventDefault();
        chip.classList.toggle("bg-steam-accent/20");
        chip.classList.toggle("border-steam-accent");
        chip.classList.toggle("text-steam-accent");
        return;
    }
});

// =========================================================================
//  SECTION 12: Logout Modal
// =========================================================================

function openLogoutModal() {
    var modal = document.getElementById("logout-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
}

function closeLogoutModal() {
    var modal = document.getElementById("logout-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
}

function confirmLogout() {
    // Clear server-side auth token and unlink session
    if (authToken) {
        fetch("/api/auth/logout?token=" + encodeURIComponent(authToken), { method: "POST" }).catch(function () {});
        // Unlink session from user on server (keeps DB data intact for next login)
        fetch("/api/user/unlink?session_id=" + encodeURIComponent(SESSION_ID), { method: "POST" }).catch(function () {});
    }

    // Clear auth-specific items but KEEP session_id and steam_id
    // — so the browser can resume conversations on re-login
    localStorage.removeItem(AUTH_TOKEN_KEY);
    savedSteamId = "";
    authToken = "";
    currentUser = null;
    applyAuthUI(null);

    // Reset sidebar UI
    if (dataStatus) {
        dataStatus.textContent = "未接入";
        dataStatus.classList.remove("bg-green-600/20", "text-green-400");
        dataStatus.classList.add("bg-gray-800");
    }
    var mobileDS = document.getElementById("mobile-data-status");
    if (mobileDS) {
        mobileDS.textContent = "未接入";
        mobileDS.classList.remove("bg-green-600/20", "text-green-400");
        mobileDS.classList.add("bg-gray-800");
    }
    if (prefStatus) prefStatus.textContent = "-";
    var mobilePref = document.getElementById("mobile-pref-status");
    if (mobilePref) mobilePref.textContent = "-";

    if (agentThought) agentThought.innerHTML = '"等待了解你的游戏喜好..."';
    var mobileThought = document.getElementById("mobile-agent-thought");
    if (mobileThought) mobileThought.innerHTML = '"等待了解你的游戏喜好..."';

    // Hide preferences display sections
    var desktopPrefs = document.getElementById("desktop-prefs-display");
    if (desktopPrefs) desktopPrefs.classList.add("hidden");
    var mobilePrefs = document.getElementById("mobile-prefs-display");
    if (mobilePrefs) mobilePrefs.classList.add("hidden");

    // Reset sync button
    function resetSyncBtn(btn, icon, text) {
        if (btn) {
            btn.classList.remove("bg-gray-600", "bg-green-600", "bg-yellow-600");
            btn.classList.add("bg-steam-light");
        }
        if (icon) {
            icon.classList.remove("fa-spinner", "fa-spin", "fa-check", "fa-exclamation-triangle");
            icon.classList.add("fa-steam");
        }
        if (text) text.textContent = "接入 Steam 游戏库";
    }
    resetSyncBtn(syncBtn, syncIcon, syncText);
    resetSyncBtn(mobileSyncBtn, mobileSyncIcon, mobileSyncText);
    isSteamConnected = false;
    isSteamSimulated = false;
    isSyncing = false;

    closeLogoutModal();
    showToast("已安全退出，会话已清除 👋", "info");

    // Regenerate session ID for a fresh chat context,
    // but DON'T delete old one — past conversations are linked to user_id in DB
    var newId = "sess_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("steam_agent_session_id", newId);

    // Reload after brief delay so user sees the toast
    setTimeout(function () {
        location.reload();
    }, 1500);
}

// =========================================================================
//  SECTION 13: Toast Notifications
// =========================================================================

/**
 * Show a floating toast notification.
 * @param {string} message - The message to display.
 * @param {string} type - "success" | "error" | "info"
 */
function showToast(message, type) {
    type = type || "info";
    var container = document.getElementById("toast-container");
    if (!container) return;

    var colors = {
        success: "border-green-500/50 bg-green-500/10 text-green-400",
        error:   "border-red-500/50 bg-red-500/10 text-red-400",
        info:    "border-steam-accent/50 bg-steam-accent/10 text-steam-accent",
    };

    var toast = document.createElement("div");
    toast.className = "pointer-events-auto border rounded-lg px-4 py-3 text-sm shadow-2xl " + (colors[type] || colors.info);
    toast.style.animation = "fadeIn 0.3s ease-out";
    toast.textContent = message;

    container.appendChild(toast);

    // Auto-dismiss after 3.5 seconds
    setTimeout(function () {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(20px)";
        toast.style.transition = "opacity 0.3s ease, transform 0.3s ease";
        setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 300);
    }, 3500);
}

// =========================================================================
//  SECTION 14: Paperclip / Attachment Button
// =========================================================================

function handleAttachmentClick() {
    // Create a hidden file input
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.style.display = "none";
    document.body.appendChild(input);

    input.addEventListener("change", async function () {
        var file = input.files[0];
        if (!file) { document.body.removeChild(input); return; }

        // Show user image in chat immediately (optimistic)
        var reader = new FileReader();
        reader.onload = function (e) {
            appendUserImageMessage(e.target.result, file.name);
        };
        reader.readAsDataURL(file);

        // Upload to server
        try {
            var formData = new FormData();
            formData.append("file", file);
            formData.append("session_id", SESSION_ID);

            var res = await fetch("/api/upload-image", {
                method: "POST",
                body: formData,
            });
            var data = await res.json();

            if (data.success) {
                // Update image src with server URL for persistence
                showToast("📎 截图已上传", "success");
            } else {
                showToast("⚠️ 上传失败，但图片已在聊天中显示", "error");
            }
        } catch (err) {
            showToast("⚠️ 上传失败：" + err.message, "error");
        }

        // AI acknowledges
        setTimeout(function () {
            showTypingIndicator();
            setTimeout(function () {
                removeTypingIndicator();
                appendAITextMessage("已收到你的截图！不过我暂时还不支持图像识别功能 😅 你可以用文字描述一下截图中是什么游戏或场景，我来帮你推荐类似的游戏。");
                scrollToBottom();
            }, 800);
        }, 400);

        document.body.removeChild(input);
    });

    input.click();
}

/** Append a user image message bubble. */
function appendUserImageMessage(dataUrl, filename) {
    var wrapper = document.createElement("div");
    wrapper.className = "flex gap-4 flex-row-reverse";
    wrapper.style.animation = "fadeIn 0.3s ease-out";

    wrapper.innerHTML =
        '<div class="w-10 h-10 rounded-full bg-steam-light flex items-center justify-center flex-shrink-0 border border-steam-accent/50 overflow-hidden">' +
        '  <img src="https://api.dicebear.com/7.x/adventurer/svg?seed=Alex&backgroundColor=2a475e" alt="avatar" class="w-full h-full object-cover">' +
        '</div>' +
        '<div class="max-w-[85%] md:max-w-[70%]">' +
        '  <p class="text-xs text-gray-400 mb-1.5 mr-1 font-medium text-right">你</p>' +
        '  <div class="bg-[#1a2d3d] p-2 rounded-2xl rounded-tr-none shadow-md border border-steam-accent/20">' +
        '    <img src="' + dataUrl + '" alt="' + escapeHtml(filename) + '" class="max-w-[300px] max-h-[300px] rounded-lg object-cover">' +
        '    <p class="text-xs text-gray-400 mt-1">' + escapeHtml(filename) + '</p>' +
        '  </div>' +
        '</div>';

    chatContainer.appendChild(wrapper);
    scrollToBottom();
}

// =========================================================================
//  SECTION 15: Game Card Click → Steam Store
// =========================================================================

/**
 * Global click handler for game cards — opens the store URL in a new tab.
 * Attached via event delegation on the chat container.
 */
chatContainer.addEventListener("click", function (e) {
    // Find the closest game card ancestor
    var card = e.target.closest(".bg-steam-dark.rounded-xl");
    if (!card) return;

    // Don't fire if the user clicked the store button (it has its own handler)
    if (e.target.closest("a") || e.target.closest("button")) return;

    // Find the store link inside the card
    var storeLink = card.querySelector("a[href]");
    if (storeLink) {
        window.open(storeLink.getAttribute("href"), "_blank");
    } else {
        // Fallback: extract game name from the h4
        var nameEl = card.querySelector("h4");
        if (nameEl) {
            window.open("https://store.steampowered.com/search/?term=" + encodeURIComponent(nameEl.textContent.trim()), "_blank");
        }
    }
});

// =========================================================================
//  SECTION 16: Event Wiring & Initialization
// =========================================================================

function init() {
    // --- Apply saved theme (day/night) ---
    initTheme();

    // --- Start real-time agent status SSE subscription ---
    startAgentStatusSubscription();

    // --- Auth: validate stored token on startup ---
    validateAuthToken();

    // --- Auth: wire up modal buttons ---
    var authSubmitBtn = document.getElementById("auth-submit-btn");
    if (authSubmitBtn) authSubmitBtn.addEventListener("click", handleAuthSubmit);

    var authToggleBtn = document.getElementById("auth-toggle-btn");
    if (authToggleBtn) {
        authToggleBtn.addEventListener("click", function () {
            openAuthModal(authMode === "login" ? "register" : "login");
        });
    }

    // Enter key in auth modal
    var authPassword = document.getElementById("auth-password");
    if (authPassword) {
        authPassword.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); handleAuthSubmit(); }
        });
    }
    var authUsername = document.getElementById("auth-username");
    if (authUsername) {
        authUsername.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); handleAuthSubmit(); }
        });
    }

    // --- Send button ---
    if (sendBtn) sendBtn.addEventListener("click", handleSend);

    // Enter key
    if (chatInput) {
        chatInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
            }
        });
        chatInput.focus();
    }

    // Desktop sync button
    if (syncBtn) syncBtn.addEventListener("click", syncSteamData);

    // Mobile sync button
    if (mobileSyncBtn) mobileSyncBtn.addEventListener("click", syncSteamData);

    // Mobile hamburger menu
    if (mobileMenuBtn) mobileMenuBtn.addEventListener("click", toggleMobileSidebar);

    // Desktop sidebar: login, settings & logout
    var desktopLoginBtn = document.getElementById("desktop-login-btn");
    if (desktopLoginBtn) desktopLoginBtn.addEventListener("click", function () { openAuthModal("login"); });
    var desktopSettingsBtn = document.getElementById("desktop-settings-btn");
    if (desktopSettingsBtn) desktopSettingsBtn.addEventListener("click", openSettingsModal);
    var desktopLogoutBtn = document.getElementById("desktop-logout-btn");
    if (desktopLogoutBtn) desktopLogoutBtn.addEventListener("click", openLogoutModal);

    // Mobile sidebar: login, settings & logout
    var mobileLoginBtn = document.getElementById("mobile-login-btn");
    if (mobileLoginBtn) mobileLoginBtn.addEventListener("click", function () {
        toggleMobileSidebar();
        setTimeout(function () { openAuthModal("login"); }, 300);
    });
    var mobileSettingsBtn = document.getElementById("mobile-settings-btn");
    if (mobileSettingsBtn) mobileSettingsBtn.addEventListener("click", function () {
        toggleMobileSidebar();  // close drawer first
        setTimeout(openSettingsModal, 300);
    });
    var mobileLogoutBtn = document.getElementById("mobile-logout-btn");
    if (mobileLogoutBtn) mobileLogoutBtn.addEventListener("click", function () {
        toggleMobileSidebar();
        setTimeout(openLogoutModal, 300);
    });

    // Attachment / paperclip button
    var attachBtn = document.getElementById("attach-btn");
    if (attachBtn) attachBtn.addEventListener("click", handleAttachmentClick);

    // Steam ID modal: submit button
    var steamIdSubmit = document.getElementById("steam-id-submit");
    if (steamIdSubmit) {
        steamIdSubmit.addEventListener("click", function () {
            var input = document.getElementById("steam-id-input");
            var steamId = input ? input.value.trim() : "";
            closeSteamIdModal();
            doSyncSteam(steamId);
        });
    }

    // Steam ID modal: skip button
    var steamIdSkip = document.getElementById("steam-id-skip");
    if (steamIdSkip) {
        steamIdSkip.addEventListener("click", function () {
            closeSteamIdModal();
            doSyncSteam("");  // fallback without ID
        });
    }

    // Steam ID modal: Enter key submits
    var steamIdInput = document.getElementById("steam-id-input");
    if (steamIdInput) {
        steamIdInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter") {
                e.preventDefault();
                var steamId = steamIdInput.value.trim();
                closeSteamIdModal();
                doSyncSteam(steamId);
            }
        });
    }

    // Render quick prompt chips
    renderQuickPrompts();

    // Load saved settings and apply to sidebar
    var savedSettings = loadSettings();
    if (savedSettings.genres.length > 0 || savedSettings.budget || savedSettings.platforms.length > 0) {
        updateSidebarPrefDisplay(savedSettings);
        updatePrefsDisplaySection(savedSettings);
    }

    // --- Escape key closes any open modal ---
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        closeAuthModal();
        closeSettingsModal();
        closeLogoutModal();
        closeSteamIdModal();
    });

    // Show DB stats on startup (non-blocking)
    fetch("/api/db-stats")
        .then(function (r) { return r.json(); })
        .then(function (stats) {
            if (stats && stats.total_games) {
                console.log("📊 游戏数据库: " + stats.total_games + " 款 | 有价格: " + stats.with_price +
                            " | 有评分: " + stats.with_review + " | 有封面: " + stats.with_image +
                            " | 热门标签: " + (stats.top_tags || []).slice(0, 3).map(function (t) { return t.tag; }).join(", "));
                // If low data, show a subtle warning
                if (stats.status === "low_data") {
                    console.warn("⚠️ 数据库游戏数量较少 (" + stats.total_games + ")，推荐可能不够精准");
                }
            }
        })
        .catch(function (err) { console.warn("DB stats unavailable:", err); });
}

// Kick off when the DOM is ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
} else {
    init();
}
