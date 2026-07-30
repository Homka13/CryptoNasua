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

        // Init platform tabs
        initPlatformTabs();

        fetchStatus();
        fetchOrders();
        setInterval(fetchStatus, 3000);
        setInterval(fetchOrders, 5000);
    }

    // ===== PLATFORM TABS =====
    let currentPlatform = 'bybit';

    function initPlatformTabs() {
        document.querySelectorAll('.platform-tab').forEach(tab => {
            tab.addEventListener('click', async () => {
                const platform = tab.dataset.platform;
                if (platform === currentPlatform) return;

                // Only allow switching between bybit and binance via these tabs
                if (platform === 'paper') return;

                setActivePlatformTab(platform);
                await switchPlatform(platform);
            });
        });
    }

    function setActivePlatformTab(platform) {
        currentPlatform = platform;
        document.querySelectorAll('.platform-tab').forEach(t => {
            t.classList.remove('active', 'bybit', 'binance', 'paper');
            if (t.dataset.platform === platform) {
                t.classList.add('active', platform);
            }
        });
    }

    async function switchPlatform(platform) {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_exchange', exchange: platform })
        });
        // Force chart update with new exchange
        if (tvWidgetInstance) {
            try { tvWidgetInstance.remove(); } catch(e) {}
            tvWidgetInstance = null;
            currentTvSymbol = '';
        }
        fetchStatus();
    }

    // ===== STATUS POLLING =====
    async function fetchStatus() {
        try {
            const token = localStorage.getItem('bot_auth_token') || authToken;
            const resp = await fetch('/api/status', {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (resp.status === 401) {
                localStorage.removeItem('bot_auth_token');
                localStorage.removeItem('bot_user_email');
                if (loginModal) loginModal.classList.remove('hidden');
                if (dashboard) dashboard.classList.add('hidden');
                return;
            }
            const data = await resp.json();

            const isPaper = data.paper_trading;
            const activeEx = data.active_exchange || 'BYBIT';
            const totalUsdt = data.usdt_total !== undefined ? data.usdt_total : data.usdt_balance;
            const freeUsdt = data.usdt_balance !== undefined ? data.usdt_balance : 0;
            const initUsdt = data.initial_capital !== undefined ? data.initial_capital : 10;
            const realUsdt = data.real_usdt_balance;

            // Sync platform tabs with server state
            if (activeEx && activeEx.toLowerCase() !== currentPlatform) {
                setActivePlatformTab(activeEx.toLowerCase());
            }

            // Balance stat
            const statBal = document.getElementById('stat-balance');
            const balSubElem = document.getElementById('stat-balance-sub');
            if (isPaper) {
                if (statBal) statBal.innerText = `$${totalUsdt.toFixed(2)} USDT (Демо)`;
                if (balSubElem) {
                    let subText = `Вільні: $${freeUsdt.toFixed(2)} | Стартові: $${initUsdt.toFixed(2)}`;
                    if (realUsdt !== null && realUsdt !== undefined) {
                        subText += ` | Real ${activeEx}: $${realUsdt.toFixed(2)}`;
                    }
                    balSubElem.innerText = subText;
                }
            } else {
                if (statBal) statBal.innerText = `$${totalUsdt.toFixed(2)} USDT (Live ${activeEx})`;
                if (balSubElem) balSubElem.innerText = `Доступний залишок ${activeEx}: $${freeUsdt.toFixed(2)} USDT`;
            }

            // Price stat
            const statPrice = document.getElementById('stat-symbol-price');
            if (statPrice) statPrice.innerText = `${data.symbol} $${data.current_price < 0.01 ? data.current_price.toFixed(8) : data.current_price.toFixed(4)}`;

            const statTf = document.getElementById('stat-timeframe');
            if (statTf) statTf.innerText = `Таймфрейм: ${data.timeframe}`;

            // Trading mode
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

            // LLM status
            const llmElem = document.getElementById('stat-llm-status');
            const providerSelect = document.getElementById('provider-select');
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

            // LLM Key Badge
            const llmBadge = document.getElementById('llm-key-status-badge');
            const llmKeyInput = document.getElementById('llm-key-input');
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
            if (llmKeyInput && !llmKeyInput.value) {
                llmKeyInput.placeholder = (data.llm_key_set && data.llm_key_masked)
                    ? `Ключ: ${data.llm_key_masked}`
                    : "Введіть ваш sk-... ключ тут";
            }

            // Sleep button
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

            // Execution mode button
            const execBtn = document.getElementById('toggle-execution-mode-btn');
            if (execBtn) {
                if (data.paper_trading) {
                    execBtn.innerText = '🧪 Демо-Торгівля ($10 Paper)';
                    execBtn.className = 'btn btn-sm btn-outline';
                    execBtn.style.boxShadow = 'none';
                } else {
                    execBtn.innerText = `⚡ LIVE Торгівля (${activeEx} Real)`;
                    execBtn.className = 'btn btn-sm btn-danger';
                    execBtn.style.boxShadow = '0 0 14px rgba(244, 63, 94, 0.5)';
                }
            }

            // Symbol select sync
            const symbolSelect = document.getElementById('symbol-select');
            if (symbolSelect && data.symbol) symbolSelect.value = data.symbol;

            // Mode badge
            const modeBadge = document.getElementById('mode-badge');
            if (modeBadge) {
                modeBadge.innerText = data.mode;
                modeBadge.className = isPaper ? 'badge badge-paper' : 'badge badge-live';
                modeBadge.style.display = 'inline-block';
            }

            // Update chart exchange label
            const chartExLabel = document.getElementById('chart-exchange-label');
            if (chartExLabel) {
                const exColor = activeEx === 'BINANCE' ? '#f0b90b' : '#f7a600';
                chartExLabel.innerText = `(${activeEx.charAt(0) + activeEx.slice(1).toLowerCase()} Spot)`;
                chartExLabel.style.color = exColor;
            }

            // Active Positions
            const positions = data.active_positions || [];
            if (data.active_position && !positions.find(p => p.symbol === data.active_position.symbol)) {
                positions.push(data.active_position);
            }
            renderActivePositions(positions);

            // Set default chart symbol
            if (!currentChartSymbol) {
                if (positions.length > 0) {
                    currentChartSymbol = positions[0].symbol;
                } else {
                    currentChartSymbol = 'SOL/USDT';
                }
            }

            updateWatchlistBar(data.scan_logs, positions);
            updateTradingViewChart(currentChartSymbol, false);

            // Controls state — sync resume/pause buttons
            const btnResume = document.getElementById('btn-resume');
            const btnPause = document.getElementById('btn-pause');
            if (btnResume && btnPause) {
                if (data.is_active) {
                    btnResume.className = 'btn btn-sm btn-success';
                    btnPause.className = 'btn btn-sm btn-outline';
                } else {
                    btnResume.className = 'btn btn-sm btn-outline';
                    btnPause.className = 'btn btn-sm btn-danger';
                }
            }

            // Scan Console
            const scanConsole = document.getElementById('scan-log-console');
            const scanLastTime = document.getElementById('scan-last-time');
            const logs = data.scan_logs || [];
            if (scanConsole && logs.length > 0) {
                if (scanLastTime) scanLastTime.innerText = `Останній аналіз: ${logs[0].time}`;
                scanConsole.innerHTML = logs.map(log => {
                    const isAiLog = log.reason && (log.reason.includes('DEEPSEEK') || log.reason.includes('GEMINI') || log.reason.includes('GPT'));
                    const isTradeExec = log.reason && (log.reason.includes('SOLD') || log.reason.includes('BOUGHT'));
                    const sigColor = log.signal === 'BUY' ? '#10b981' : (log.signal === 'REJECTED' ? '#f43f5e' : (log.signal === 'SELL' ? '#f43f5e' : '#8896b4'));
                    const priceFormatted = log.price < 0.01 ? log.price.toFixed(8) : log.price.toFixed(4);

                    let bgStyle = 'border-bottom: 1px solid rgba(255,255,255,0.04); padding-bottom: 4px;';
                    if (isTradeExec) {
                        bgStyle = `background: ${log.signal === 'BUY' ? 'rgba(16,185,129,0.10)' : 'rgba(244,63,94,0.10)'}; border-left: 3px solid ${log.signal === 'BUY' ? '#10b981' : '#f43f5e'}; padding: 4px 8px; border-radius: 4px;`;
                    } else if (isAiLog) {
                        bgStyle = 'background: rgba(139, 92, 246, 0.12); border-left: 3px solid #8b5cf6; padding: 4px 8px; border-radius: 4px;';
                    }
                    return `<div style="display:flex;gap:8px;margin-bottom:3px;align-items:center;flex-wrap:wrap;${bgStyle}">
                        <span style="color:#5c6a82;font-size:0.78rem;">[${log.time}]</span>
                        <span style="color:#93bbfd;font-weight:700;">${log.symbol} ($${priceFormatted})</span>
                        <span>| <strong style="color:${sigColor};">${log.signal}</strong></span>
                        <span style="color:#8896b4;flex:1;">| ${log.reason}</span>
                    </div>`;
                }).join('');
            }
        } catch (err) {
            console.error("Status polling error:", err);
        }
    }

    // ===== MULTI-POSITION RENDERING =====
    function renderActivePositions(positions) {
        const container = document.getElementById('active-positions-container');
        const countBadge = document.getElementById('positions-count-badge');
        if (!container) return;

        if (countBadge) countBadge.innerText = positions.length;

        if (!positions || positions.length === 0) {
            container.innerHTML = '<div class="empty-positions">Немає відкритих угод — сканування ринку триває...</div>';
            return;
        }

        container.innerHTML = '<div class="positions-grid">' + positions.map(pos => {
            const currPrice = pos.current_price || pos.entry_price;
            const pnl = ((currPrice - pos.entry_price) / pos.entry_price) * 100;
            const pnlUsdt = (currPrice - pos.entry_price) * (pos.amount || 0);
            const pnlClass = pnl >= 0 ? 'profit' : 'loss';
            const pnlColor = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

            const entryTimeVal = pos.entry_time || pos.timestamp;
            const entryTimestamp = entryTimeVal ? (entryTimeVal > 1e11 ? entryTimeVal : entryTimeVal * 1000) : Date.now();
            const holdMin = Math.max(0, (Date.now() - entryTimestamp) / (1000 * 60));
            const holdStr = holdMin < 60 ? `${holdMin.toFixed(1)}хв` : `${(holdMin / 60).toFixed(1)}год`;

            const posRsi = pos.rsi || 50;
            const posTrend = pos.trend || '—';
            const rsiColor = posRsi <= 30 ? '#10b981' : (posRsi >= 70 ? '#f43f5e' : '#93bbfd');
            const trendColor = posTrend === 'BULLISH' ? '#10b981' : (posTrend === 'BEARISH' ? '#f43f5e' : '#8896b4');
            const priceFmt = (p) => p < 0.01 ? p.toFixed(8) : p.toFixed(4);

            return `
            <div class="glass-card position-card ${pnlClass}">
                <div class="position-card-header">
                    <span class="symbol">📌 ${pos.symbol}</span>
                    <span style="font-size:0.78rem;color:var(--text-muted);">⏱ ${holdStr}</span>
                </div>
                <div class="position-card-metrics">
                    <div><span class="metric-label">К-сть:</span> <span class="metric-value">${(pos.amount || 0).toFixed(4)}</span></div>
                    <div><span class="metric-label">Вхід:</span> <span class="metric-value">$${priceFmt(pos.entry_price)}</span></div>
                    <div><span class="metric-label">Поточна:</span> <span class="metric-value">$${priceFmt(currPrice)}</span></div>
                    <div><span class="metric-label">Order ID:</span> <span class="metric-value" style="font-family:monospace;font-size:0.75rem;">${pos.order_id || '—'}</span></div>
                </div>
                <div class="position-card-indicators">
                    <span>RSI: <strong style="color:${rsiColor}">${posRsi.toFixed(1)}</strong></span>
                    <span>Тренд: <strong style="color:${trendColor}">${posTrend}</strong></span>
                    ${pos.ema_fast ? `<span>EMA20: <strong>$${priceFmt(pos.ema_fast)}</strong></span>` : ''}
                    ${pos.ema_slow ? `<span>EMA50: <strong>$${priceFmt(pos.ema_slow)}</strong></span>` : ''}
                </div>
                <div class="position-card-footer">
                    <span class="position-pnl" style="color:${pnlColor};">
                        ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}% (${pnlUsdt >= 0 ? '+' : ''}$${pnlUsdt.toFixed(4)})
                    </span>
                    <button class="btn btn-xs btn-danger close-pos-btn" data-symbol="${pos.symbol}">
                        🔴 Закрити
                    </button>
                </div>
            </div>`;
        }).join('') + '</div>';

        // Attach close handlers
        container.querySelectorAll('.close-pos-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const sym = btn.dataset.symbol;
                if (confirm(`Закрити позицію ${sym}?`)) {
                    await fetch('/api/control', {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ action: 'close_position', symbol: sym })
                    });
                    fetchStatus();
                }
            });
        });
    }

    // ===== TRADE ACTIONS & ORDERS HISTORY =====
    async function fetchOrders() {
        try {
            const resp = await fetch('/api/orders', {
                headers: { 'Authorization': `Bearer ${getAuthToken()}` }
            });
            if (!resp.ok) return;
            const data = await resp.json();

            const tradeActions = data.trade_actions || [];
            const aiVerdicts = data.ai_verdicts || [];
            const listContainer = document.getElementById('trade-actions-list');
            const tradeCount = document.getElementById('trade-actions-count');
            const aiCount = document.getElementById('ai-verdicts-count');

            if (tradeCount) tradeCount.innerText = tradeActions.length;
            if (aiCount) aiCount.innerText = `${aiVerdicts.length} 🤖`;

            if (!listContainer) return;

            const priceFmt = (p) => (p && p < 0.01) ? p.toFixed(8) : (p || 0).toFixed(4);

            let html = '';

            // Render Trade Actions (BUY/SELL)
            if (tradeActions.length === 0) {
                html += '<div class="empty-positions" style="margin:0;border:none;">Очікування перших торгових дій...</div>';
            } else {
                html += tradeActions.map(ta => {
                    const isBuy = ta.side === 'BUY';
                    const isSell = ta.side === 'SELL';
                    const rowClass = isBuy ? 'buy-action' : `sell-action${ta.pnl_pct > 0 ? ' profitable' : ''}`;
                    const badgeClass = isBuy ? 'buy' : 'sell';
                    const badgeIcon = isBuy ? '🟢' : '🔴';
                    const pnlStr = isSell && ta.pnl_pct !== null && ta.pnl_pct !== undefined
                        ? `<span class="trade-action-pnl" style="color:${ta.pnl_pct >= 0 ? '#10b981' : '#f43f5e'};">${ta.pnl_pct >= 0 ? '+' : ''}${ta.pnl_pct.toFixed(2)}%</span>`
                        : '';

                    return `<div class="trade-action-row ${rowClass}">
                        <span class="trade-action-time">${ta.time || '—'}</span>
                        <span class="trade-action-badge ${badgeClass}">${badgeIcon} ${ta.side}</span>
                        <span class="trade-action-symbol">${ta.symbol || '—'}</span>
                        <span class="trade-action-details">
                            ${ta.amount ? ta.amount.toFixed(4) : '—'} × $${priceFmt(ta.price)}
                            ${isSell && ta.entry_price ? ` | Entry: $${priceFmt(ta.entry_price)}` : ''}
                        </span>
                        ${pnlStr}
                    </div>`;
                }).join('');
            }

            // Render AI Verdicts (compact, below trade actions)
            if (aiVerdicts.length > 0) {
                html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.06);font-size:0.72rem;color:var(--text-muted);margin-bottom:4px;">🤖 ШІ Вердикти</div>';
                html += aiVerdicts.slice(0, 10).map(v => {
                    const isConfirmed = v.status === 'CONFIRMED';
                    const icon = isConfirmed ? '✅' : '🛑';
                    return `<div class="trade-action-row ai-verdict" style="padding:5px 10px;font-size:0.76rem;">
                        <span class="trade-action-time">${v.time || '—'}</span>
                        <span class="trade-action-badge ai">🤖 AI</span>
                        <span class="trade-action-symbol">${v.symbol || '—'}</span>
                        <span class="trade-action-details" style="color:${isConfirmed ? '#86efac' : '#fca5a5'};">${icon} ${v.reason || ''}</span>
                    </div>`;
                }).join('');
            }

            listContainer.innerHTML = html;
        } catch (err) {
            console.error("Orders polling error:", err);
        }
    }

    // ===== CONTROL HANDLERS =====
    document.getElementById('btn-resume')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'resume' })
        });
        fetchStatus();
    });

    document.getElementById('btn-pause')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'pause' })
        });
        fetchStatus();
    });

    document.getElementById('toggle-execution-mode-btn')?.addEventListener('click', async () => {
        const btn = document.getElementById('toggle-execution-mode-btn');
        const isPaperNow = btn ? btn.innerText.includes('Демо') : true;
        const confirmMsg = isPaperNow ? "⚠️ УВАГА: Ви вмикаєте РЕАЛЬНУ торгівлю на CEX! Продовжити?" : "Перемкнути назад у режим Демо-Торгівлі ($10 Paper)?";
        if (confirm(confirmMsg)) {
            await fetch('/api/control', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'set_execution_mode', mode: isPaperNow ? 'live' : 'paper' })
            });
            fetchStatus();
        }
    });

    document.getElementById('btn-exec-paper')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_execution_mode', mode: 'paper' })
        });
        fetchStatus();
    });

    document.getElementById('btn-exec-live')?.addEventListener('click', async () => {
        if (confirm("⚠️ УВАГА: Ви вмикаєте РЕАЛЬНУ торгівлю на Bybit! Продовжити?")) {
            await fetch('/api/control', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'set_execution_mode', mode: 'live' })
            });
            fetchStatus();
        }
    });

    document.getElementById('stat-card-llm')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'toggle_llm' })
        });
        fetchStatus();
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
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
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

    function getAuthToken() {
        return localStorage.getItem('bot_auth_token') || authToken || '';
    }

    document.getElementById('btn-mode-chill')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'chill' })
        });
        fetchStatus();
    });

    document.getElementById('btn-mode-hunt')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'hunt' })
        });
        fetchStatus();
    });

    document.getElementById('provider-select')?.addEventListener('change', async (e) => {
        const provider = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_llm_provider', provider })
        });
        fetchStatus();
    });

    document.getElementById('toggle-sleep-btn')?.addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'toggle_prevent_sleep' })
        });
        fetchStatus();
    });

    document.getElementById('symbol-select')?.addEventListener('change', async (e) => {
        const symbol = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'change_symbol', symbol })
        });
        fetchStatus();
    });
}

// ===== TRADINGVIEW CHART =====
let currentChartSymbol = 'SOL/USDT';
let activeWatchlist = ['SOL/USDT', 'WLD/USDT', 'PUMP/USDT', 'PEPE/USDT', 'SHIB/USDT', 'CHIP/USDT', 'BIRB/USDT', 'BTC/USDT', 'ETH/USDT'];
let tvWidgetInstance = null;
let currentTvSymbol = '';

function getTvExchangePrefix() {
    const ex = (currentPlatform || 'bybit').toUpperCase();
    return ex === 'BINANCE' ? 'BINANCE:' : 'BYBIT:';
}

function updateWatchlistBar(scannedLogs, activePositions) {
    const bar = document.getElementById('watchlist-bar');
    if (!bar) return;

    const symbolsSet = new Set(activeWatchlist);
    if (activePositions && activePositions.length > 0) {
        activePositions.forEach(p => { if (p.symbol) symbolsSet.add(p.symbol); });
    }
    if (scannedLogs && scannedLogs.length > 0) {
        scannedLogs.forEach(l => { if (l.symbol && !l.symbol.includes('AUTO')) symbolsSet.add(l.symbol); });
    }

    const symbols = Array.from(symbolsSet).slice(0, 15);
    bar.innerHTML = '';

    const activeSyms = new Set((activePositions || []).map(p => p.symbol));

    symbols.forEach(sym => {
        const btn = document.createElement('button');
        const isSelected = (sym === currentChartSymbol);
        const isActivePos = activeSyms.has(sym);

        let btnClass = 'btn btn-sm ';
        if (isSelected) btnClass += 'btn-primary';
        else if (isActivePos) btnClass += 'btn-success';
        else btnClass += 'btn-outline';

        btn.className = btnClass;
        btn.style.borderRadius = '20px';
        btn.style.whiteSpace = 'nowrap';
        btn.style.padding = '4px 14px';
        btn.style.fontSize = '0.8rem';
        btn.style.fontWeight = 'bold';

        btn.innerHTML = `${isActivePos ? '📌 ' : ''}${sym}`;
        btn.onclick = () => {
            currentChartSymbol = sym;
            updateWatchlistBar(scannedLogs, activePositions);
            updateTradingViewChart(sym, true);
        };
        bar.appendChild(btn);
    });
}

function updateTradingViewChart(symbolStr, forceUpdate = false) {
    if (typeof TradingView === 'undefined') return;

    let cleanSym = (symbolStr || 'SOL/USDT').replace('/', '').toUpperCase();
    if (cleanSym.includes('AUTO')) cleanSym = 'SOLUSDT';

    const prefix = getTvExchangePrefix();
    const tvSymbol = `${prefix}${cleanSym}`;
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
            "toolbar_bg": "#0b1424",
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
