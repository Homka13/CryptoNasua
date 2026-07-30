function initApp() {
    const loginModal = document.getElementById('login-modal');
    const dashboard = document.getElementById('dashboard');
    const loginBtn = document.getElementById('login-btn');
    const registerBtn = document.getElementById('register-btn');
    const tabLoginBtn = document.getElementById('tab-login-btn');
    const tabRegisterBtn = document.getElementById('tab-register-btn');
    const loginFormBox = document.getElementById('login-form-box');
    const registerFormBox = document.getElementById('register-form-box');
    const emailInput = document.getElementById('email-input');
    const passwordInput = document.getElementById('password-input');
    const regEmailInput = document.getElementById('reg-email-input');
    const regPasswordInput = document.getElementById('reg-password-input');
    const regConfirmPasswordInput = document.getElementById('reg-confirm-password-input');
    const loginError = document.getElementById('login-error');
    const logoutBtn = document.getElementById('logout-btn');
    const userEmailDisplay = document.getElementById('user-email-display');

    let authToken = localStorage.getItem('bot_auth_token');
    let userEmail = localStorage.getItem('bot_user_email');

    if (authToken && userEmail) {
        showDashboard();
    }

    // Auth Tabs Switching
    tabLoginBtn?.addEventListener('click', () => {
        tabLoginBtn.className = "btn btn-sm btn-primary";
        tabRegisterBtn.className = "btn btn-sm btn-outline";
        loginFormBox?.classList.remove('hidden');
        registerFormBox?.classList.add('hidden');
        if (loginError) loginError.style.display = 'none';
    });

    tabRegisterBtn?.addEventListener('click', () => {
        tabRegisterBtn.className = "btn btn-sm btn-primary";
        tabLoginBtn.className = "btn btn-sm btn-outline";
        registerFormBox?.classList.remove('hidden');
        loginFormBox?.classList.add('hidden');
        if (loginError) loginError.style.display = 'none';
    });

    // Login Submission
    loginBtn?.addEventListener('click', async () => {
        const email = emailInput ? emailInput.value.trim() : '';
        const password = passwordInput ? passwordInput.value : '';
        if (!email) {
            showError("Будь ласка, введіть ваш Email.");
            return;
        }

        try {
            if (loginBtn) loginBtn.disabled = true;
            const endpoint = password ? '/api/auth/login' : '/api/auth/google';
            const bodyPayload = password ? { email, password } : { email };

            const resp = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(bodyPayload)
            });
            const data = await resp.json();

            if (data.success) {
                authToken = data.token;
                userEmail = data.email;
                localStorage.setItem('bot_auth_token', authToken);
                localStorage.setItem('bot_user_email', userEmail);
                showDashboard();
            } else {
                showError(data.error || "Помилка авторизації");
            }
        } catch (err) {
            showError("Не вдалося з'єднатися з сервером");
        } finally {
            if (loginBtn) loginBtn.disabled = false;
        }
    });

    // Registration Submission
    registerBtn?.addEventListener('click', async () => {
        const email = regEmailInput ? regEmailInput.value.trim() : '';
        const password = regPasswordInput ? regPasswordInput.value : '';
        const confirmPwd = regConfirmPasswordInput ? regConfirmPasswordInput.value : '';

        if (!email) {
            showError("Введіть ваші дані для реєстрації.");
            return;
        }
        if (password.length < 6) {
            showError("Пароль має бути щонайменше 6 символів.");
            return;
        }
        if (password !== confirmPwd) {
            showError("Паролі не співпадають!");
            return;
        }

        try {
            if (registerBtn) registerBtn.disabled = true;
            const resp = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await resp.json();

            if (data.success) {
                authToken = data.token;
                userEmail = data.email;
                localStorage.setItem('bot_auth_token', authToken);
                localStorage.setItem('bot_user_email', userEmail);
                showDashboard();
            } else {
                showError(data.error || "Помилка реєстрації");
            }
        } catch (err) {
            showError("Не вдалося з'єднатися з сервером при реєстрації");
        } finally {
            if (registerBtn) registerBtn.disabled = false;
        }
    });

    logoutBtn?.addEventListener('click', () => {
        localStorage.removeItem('bot_auth_token');
        localStorage.removeItem('bot_user_email');
        authToken = null;
        userEmail = null;
        if (loginModal) {
            loginModal.classList.remove('hidden');
            loginModal.style.display = 'flex';
        }
        if (dashboard) {
            dashboard.classList.add('hidden');
            dashboard.style.display = 'none';
        }
    });

    function showError(msg) {
        if (loginError) {
            loginError.innerText = msg;
            loginError.style.display = 'block';
        } else {
            alert(msg);
        }
    }

    function showDashboard() {
        if (loginModal) {
            loginModal.classList.add('hidden');
            loginModal.style.display = 'none';
        }
        if (dashboard) {
            dashboard.classList.remove('hidden');
            dashboard.style.display = 'block';
        }
        if (userEmailDisplay) userEmailDisplay.innerText = userEmail;

        fetchStatus();
        fetchOrders();
        setInterval(fetchStatus, 3000);
        setInterval(fetchOrders, 5000);
    }

    async function fetchStatus() {
        try {
            const resp = await fetch('/api/status', {
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (resp.status === 401) {
                localStorage.removeItem('bot_auth_token');
                localStorage.removeItem('bot_user_email');
                if (loginModal) loginModal.classList.remove('hidden');
                if (dashboard) dashboard.classList.add('hidden');
                return;
            }
            const data = await resp.json();

            // Update UI Stats
            const isPaper = data.paper_trading;
            const totalUsdt = data.usdt_total !== undefined ? data.usdt_total : data.usdt_balance;
            const freeUsdt = data.usdt_balance !== undefined ? data.usdt_balance : 0;
            const initUsdt = data.initial_capital !== undefined ? data.initial_capital : 10;
            const realUsdt = data.real_usdt_balance;

            const statBal = document.getElementById('stat-balance');
            const balSubElem = document.getElementById('stat-balance-sub');

            if (isPaper) {
                if (statBal) statBal.innerText = `$${totalUsdt.toFixed(2)} USDT (Демо)`;
                if (balSubElem) {
                    let subText = `Вільні кошти: $${freeUsdt.toFixed(2)}`;
                    if (realUsdt !== null && realUsdt !== undefined) {
                        subText += ` | Real Bybit: $${realUsdt.toFixed(2)} USDT`;
                    } else {
                        subText += ` | Початкові: $${initUsdt.toFixed(2)}`;
                    }
                    balSubElem.innerText = subText;
                }
            } else {
                if (statBal) statBal.innerText = `$${totalUsdt.toFixed(2)} USDT (Live Bybit)`;
                if (balSubElem) {
                    balSubElem.innerText = `Доступний залишок Bybit: $${freeUsdt.toFixed(2)} USDT`;
                }
            }

            const statPrice = document.getElementById('stat-symbol-price');
            if (statPrice) statPrice.innerText = `${data.symbol} $${data.current_price < 0.01 ? data.current_price.toFixed(8) : data.current_price.toFixed(4)}`;

            const statTf = document.getElementById('stat-timeframe');
            if (statTf) statTf.innerText = `Таймфрейм: ${data.timeframe}`;
            
            // Execution Mode Buttons Sync
            const btnExecPaper = document.getElementById('btn-exec-paper');
            const btnExecLive = document.getElementById('btn-exec-live');
            if (btnExecPaper && btnExecLive) {
                if (isPaper) {
                    btnExecPaper.className = 'btn-mode active-chill';
                    btnExecLive.className = 'btn-mode';
                } else {
                    btnExecPaper.className = 'btn-mode';
                    btnExecLive.className = 'btn-mode active-hunt';
                }
            }

            // Trading mode stat & buttons
            const modeElem = document.getElementById('stat-trading-mode');
            const modeSubElem = document.getElementById('stat-trading-mode-sub');
            const btnChill = document.getElementById('btn-mode-chill');
            const btnHunt = document.getElementById('btn-mode-hunt');

            if (data.trading_mode === 'chill') {
                if (modeElem) modeElem.innerText = '🦝 CHILL (90%)';
                if (modeSubElem) modeSubElem.innerText = 'Снайпер: 1-3 ідеальні угоди/день';
                if (btnChill) btnChill.className = 'btn-mode active-chill';
                if (btnHunt) btnHunt.className = 'btn-mode';
            } else {
                if (modeElem) modeElem.innerText = '🔥 HUNT (60%)';
                if (modeSubElem) modeSubElem.innerText = 'Агресор: 10-15 угод на тренді';
                if (btnChill) btnChill.className = 'btn-mode';
                if (btnHunt) btnHunt.className = 'btn-mode active-hunt';
            }

            const llmElem = document.getElementById('stat-llm-status');
            const providerSelect = document.getElementById('provider-select');
            const llmKeyInput = document.getElementById('llm-key-input');

            if (llmElem) {
                if (data.llm_enabled) {
                    llmElem.innerText = `АКТИВНИЙ (${data.llm_provider})`;
                    llmElem.className = 'stat-value text-green';
                } else {
                    llmElem.innerText = 'ВИМКНЕНО';
                    llmElem.className = 'stat-value text-red';
                }
            }
            if (providerSelect && data.llm_provider) {
                providerSelect.value = data.llm_provider.toLowerCase();
            }

            const llmBadge = document.getElementById('llm-key-status-badge');
            if (llmBadge) {
                if (data.llm_key_set && data.llm_key_masked) {
                    llmBadge.innerText = `🟢 Підключено: ${data.llm_key_masked}`;
                    llmBadge.style.background = 'rgba(72, 187, 120, 0.2)';
                    llmBadge.style.color = '#48bb78';
                    llmBadge.style.borderColor = 'rgba(72, 187, 120, 0.4)';
                } else {
                    llmBadge.innerText = '🔴 Ключ не налаштовано';
                    llmBadge.style.background = 'rgba(245, 101, 101, 0.2)';
                    llmBadge.style.color = '#f56565';
                    llmBadge.style.borderColor = 'rgba(245, 101, 101, 0.4)';
                }
            }

            const sleepBtn = document.getElementById('toggle-sleep-btn');
            if (sleepBtn) {
                if (data.prevent_sleep) {
                    sleepBtn.innerText = '💤 24/7 АКТИВНИЙ';
                    sleepBtn.className = 'btn btn-sm btn-success';
                } else {
                    sleepBtn.innerText = '🌙 ЗВИЧАЙНИЙ РЕЖИМ';
                    sleepBtn.className = 'btn btn-sm btn-outline';
                }
            }

            const execBtn = document.getElementById('toggle-execution-mode-btn');
            if (execBtn) {
                if (data.paper_trading) {
                    execBtn.innerText = '🧪 Демо-Торгівля ($10 Paper)';
                    execBtn.className = 'btn btn-sm btn-outline';
                    execBtn.style.boxShadow = 'none';
                } else {
                    execBtn.innerText = '⚡ LIVE Торгівля (Bybit Real)';
                    execBtn.className = 'btn btn-sm btn-danger';
                    execBtn.style.boxShadow = '0 0 12px rgba(239, 68, 68, 0.5)';
                }
            }

            if (llmKeyInput && !llmKeyInput.value) {
                if (data.llm_key_set && data.llm_key_masked) {
                    llmKeyInput.placeholder = `Ключ: ${data.llm_key_masked}`;
                } else {
                    llmKeyInput.placeholder = "Введіть ваш sk-... ключ тут";
                }
            }

            const exSelect = document.getElementById('exchange-select');
            if (exSelect && data.active_exchange) {
                exSelect.value = data.active_exchange.toLowerCase();
            }

            const symbolSelect = document.getElementById('symbol-select');
            if (symbolSelect && data.symbol) {
                symbolSelect.value = data.symbol;
            }

            // Mode badge
            const modeBadge = document.getElementById('mode-badge');
            if (modeBadge) modeBadge.innerText = data.mode;

            // Active Position
            // Update Watchlist Bar and stable TradingView chart for selected symbol
            if (!currentChartSymbol) {
                if (data.active_position && data.active_position.symbol) {
                    currentChartSymbol = data.active_position.symbol;
                } else {
                    currentChartSymbol = 'SOL/USDT';
                }
            }
            
            updateWatchlistBar(data.scan_logs, data.active_position);
            updateTradingViewChart(currentChartSymbol, false);

            const posContent = document.getElementById('active-position-content');
            if (posContent) {
                if (data.active_position) {
                    const pos = data.active_position;
                    const currPrice = data.latest_price || pos.entry_price;
                    const pnl = ((currPrice - pos.entry_price) / pos.entry_price) * 100;
                    const pnlClass = pnl >= 0 ? 'text-green' : 'text-red';
                    posContent.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                            <div>
                                <div style="font-size: 1.1rem; margin-bottom: 4px;">
                                    <strong>${pos.symbol}</strong> — ${pos.amount.toFixed(4)} монет
                                </div>
                                <div style="font-size: 0.85rem; color: #a0aec0;">
                                    Ціна входу: $${pos.entry_price.toFixed(4)}
                                </div>
                                <div class="${pnlClass}" style="font-size: 1.2rem; font-weight: 700; margin-top: 4px;">
                                    PnL: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%
                                </div>
                            </div>
                            <button id="close-position-btn" class="btn btn-sm btn-danger" style="font-weight: bold; padding: 8px 16px;">
                                🔴 Закрити Позицію
                            </button>
                        </div>
                    `;

                    document.getElementById('close-position-btn')?.addEventListener('click', async () => {
                        if (confirm(`Ви дійсно бажаєте вручну закрити позицію ${pos.symbol}?`)) {
                            await fetch('/api/control', {
                                method: 'POST',
                                headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
                                body: JSON.stringify({ action: 'close_position' })
                            });
                            fetchStatus();
                        }
                    });
                } else {
                    posContent.innerHTML = `<p class="text-muted" style="margin: 0;">Немає відкритих угод (Сканування ринку...)</p>`;
                }
            }

            // Controls state
            const toggleActiveBtn = document.getElementById('toggle-active-btn');
            if (toggleActiveBtn) {
                if (data.is_active) {
                    toggleActiveBtn.innerText = "▶️ Бот працює";
                    toggleActiveBtn.className = "btn btn-success";
                } else {
                    toggleActiveBtn.innerText = "🛑 Бот на паузі";
                    toggleActiveBtn.className = "btn btn-warning";
                }
            }

            // Live Scan Console Stream
            const scanConsole = document.getElementById('scan-log-console');
            const scanLastTime = document.getElementById('scan-last-time');
            const logs = data.scan_logs || [];
            
            if (scanConsole && logs.length > 0) {
                if (scanLastTime) {
                    scanLastTime.innerText = `Останній аналіз: ${logs[0].time}`;
                }
                scanConsole.innerHTML = logs.map(log => {
                    const isAiLog = log.reason && log.reason.includes('DEEPSEEK');
                    const sigColor = log.signal === 'BUY' ? '#48bb78' : (log.signal === 'REJECTED' ? '#f56565' : (log.signal === 'SELL' ? '#f56565' : '#a0aec0'));
                    const priceFormatted = log.price < 0.01 ? log.price.toFixed(8) : log.price.toFixed(4);
                    const bgStyle = isAiLog ? 'background: rgba(147, 51, 234, 0.15); border-left: 3px solid #a855f7; padding: 4px 8px; border-radius: 4px;' : 'border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 4px;';
                    return `
                        <div style="display: flex; gap: 10px; margin-bottom: 4px; align-items: center; flex-wrap: wrap; ${bgStyle}">
                            <span style="color: #718096; font-size: 0.8rem;">[${log.time}]</span>
                            <span style="color: #63b3ed; font-weight: bold;">🔎 ${log.symbol} ($${priceFormatted})</span>
                            <span>| Вердикт: <strong style="color: ${sigColor};">${log.signal}</strong></span>
                            <span style="color: #cbd5e0; flex: 1;">| ${log.reason}</span>
                        </div>
                    `;
                }).join('');
            }
        } catch (err) {
            console.error("Status polling error:", err);
        }
    }

    async function fetchOrders() {
        try {
            const resp = await fetch('/api/orders', {
                headers: { 'Authorization': `Bearer ${authToken}` }
            });
            if (!resp.ok) return;
            const data = await resp.json();

            const tbody = document.getElementById('orders-table-body');
            if (!tbody) return;
            const orders = Array.isArray(data) ? data : (data.order_history || []);

            if (orders.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="text-center">Очікування першої угоди...</td></tr>`;
                return;
            }

            tbody.innerHTML = orders.map(ord => {
                const dateStr = ord.time || (ord.timestamp ? new Date(ord.timestamp).toLocaleTimeString() : 'N/A');
                const isReject = ord.status === 'REJECTED' || ord.side === 'reject';
                const sideClass = isReject ? 'text-red' : (ord.side === 'buy' ? 'text-green' : 'text-red');
                const badgeClass = isReject ? 'alert-danger' : 'badge-paper';
                const statusLabel = isReject ? '🛑 REJECTED' : (ord.status || 'CLOSED');
                const reasonText = ord.reason || '🟢 Виконано за алгоритмом';
                const priceFormatted = ord.price ? (ord.price < 0.01 ? ord.price.toFixed(8) : ord.price.toFixed(4)) : '0.00';
                const amountFormatted = ord.amount ? ord.amount.toFixed(4) : '-';

                return `
                    <tr>
                        <td>${dateStr}</td>
                        <td><strong>${ord.symbol || 'N/A'}</strong></td>
                        <td class="${sideClass}"><strong>${(ord.side || 'BUY').toUpperCase()}</strong></td>
                        <td>$${priceFormatted}</td>
                        <td>${amountFormatted}</td>
                        <td><span class="badge ${badgeClass}" style="${isReject ? 'background: rgba(239,68,68,0.2); color: #f87171;' : ''}">${statusLabel}</span></td>
                        <td style="font-size: 0.85rem; color: ${isReject ? '#f87171' : '#a0aec0'};">${reasonText}</td>
                    </tr>
                `;
            }).join('');
        } catch (err) {
            console.error("Orders polling error:", err);
        }
    }

    // Control Handlers
    document.getElementById('toggle-active-btn')?.addEventListener('click', async () => {
        const btn = document.getElementById('toggle-active-btn');
        const isCurrentlyActive = btn?.innerText.includes("працює");
        const action = isCurrentlyActive ? 'pause' : 'resume';

        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
        fetchStatus();
    });

    document.getElementById('btn-exec-paper')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_execution_mode', mode: 'paper' })
        });
        fetchStatus();
    });

    document.getElementById('btn-exec-live')?.addEventListener('click', async () => {
        if (confirm("⚠️ УВАГА: Ви вмикаєте РЕАЛЬНУ торгівлю на Bybit! Продовжити?")) {
            await fetch('/api/control', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'set_execution_mode', mode: 'live' })
            });
            fetchStatus();
        }
    });

    document.getElementById('save-llm-key-btn')?.addEventListener('click', async () => {
        const keyInput = document.getElementById('llm-key-input');
        const key = keyInput ? keyInput.value.trim() : '';
        if (!key) {
            alert("Будь ласка, введіть API ключ!");
            return;
        }
        const resp = await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_llm_key', key })
        });
        const resData = await resp.json();
        if (resData.success) {
            alert("✅ API Ключ ШІ успішно збережено!");
            if (keyInput) keyInput.value = '';
            fetchStatus();
        } else {
            alert("❌ Помилка збереження ключа: " + (resData.error || "Невідома помилка"));
        }
    });

    document.getElementById('btn-mode-chill')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'chill' })
        });
        fetchStatus();
    });

    document.getElementById('btn-mode-hunt')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'hunt' })
        });
        fetchStatus();
    });

    document.getElementById('provider-select')?.addEventListener('change', async (e) => {
        const provider = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_llm_provider', provider })
        });
        fetchStatus();
    });

    document.getElementById('exchange-select')?.addEventListener('change', async (e) => {
        const exchange = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_exchange', exchange })
        });
        fetchStatus();
    });

    document.getElementById('toggle-execution-mode-btn')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'toggle_mode' })
        });
        fetchStatus();
    });

    document.getElementById('toggle-sleep-btn')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'toggle_prevent_sleep' })
        });
        fetchStatus();
    });

    document.getElementById('symbol-select')?.addEventListener('change', async (e) => {
        const symbol = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'change_symbol', symbol })
        });
        fetchStatus();
    });
}

let currentChartSymbol = 'SOL/USDT';
let activeWatchlist = ['SOL/USDT', 'WLD/USDT', 'PUMP/USDT', 'PEPE/USDT', 'SHIB/USDT', 'CHIP/USDT', 'BIRB/USDT', 'BTC/USDT', 'ETH/USDT'];
let tvWidgetInstance = null;
let currentTvSymbol = '';

function updateWatchlistBar(scannedLogs, activePosition) {
    const bar = document.getElementById('watchlist-bar');
    if (!bar) return;

    const symbolsSet = new Set(activeWatchlist);
    if (activePosition && activePosition.symbol) {
        symbolsSet.add(activePosition.symbol);
    }
    if (scannedLogs && scannedLogs.length > 0) {
        scannedLogs.forEach(l => { if (l.symbol && !l.symbol.includes('AUTO')) symbolsSet.add(l.symbol); });
    }

    const symbols = Array.from(symbolsSet).slice(0, 15);
    bar.innerHTML = '';

    symbols.forEach(sym => {
        const btn = document.createElement('button');
        const isSelected = (sym === currentChartSymbol);
        const isActivePos = (activePosition && activePosition.symbol === sym);
        
        let btnClass = isSelected ? 'btn btn-sm btn-primary' : 'btn btn-sm btn-outline';
        if (isActivePos) btnClass = 'btn btn-sm btn-success';

        btn.className = btnClass;
        btn.style.borderRadius = '20px';
        btn.style.whiteSpace = 'nowrap';
        btn.style.padding = '4px 14px';
        btn.style.fontSize = '0.8rem';
        btn.style.fontWeight = 'bold';
        
        btn.innerHTML = `${isActivePos ? '📌 ' : ''}${sym}`;
        btn.onclick = () => {
            currentChartSymbol = sym;
            updateWatchlistBar(scannedLogs, activePosition);
            updateTradingViewChart(sym, true);
        };
        bar.appendChild(btn);
    });
}

function updateTradingViewChart(symbolStr, forceUpdate = false) {
    if (typeof TradingView === 'undefined') return;
    
    let cleanSym = (symbolStr || 'SOL/USDT').replace('/', '').toUpperCase();
    if (cleanSym.includes('AUTO')) cleanSym = 'SOLUSDT';
    
    const tvSymbol = `BYBIT:${cleanSym}`;
    if (!forceUpdate && tvSymbol === currentTvSymbol && tvWidgetInstance) return;

    currentTvSymbol = tvSymbol;
    const titleElem = document.getElementById('chart-pair-title');
    if (titleElem) titleElem.innerText = tvSymbol;

    try {
        tvWidgetInstance = new TradingView.widget({
            "autosize": true,
            "symbol": tvSymbol,
            "interval": "15",
            "timezone": "Etc/UTC",
            "theme": "dark",
            "style": "1",
            "locale": "uk",
            "toolbar_bg": "#0f172a",
            "enable_publishing": false,
            "allow_symbol_change": true,
            "container_id": "tradingview_chart_element"
        });
    } catch (err) {
        console.error("TradingView widget init error:", err);
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}
