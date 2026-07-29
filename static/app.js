document.addEventListener('DOMContentLoaded', () => {
    const loginModal = document.getElementById('login-modal');
    const dashboard = document.getElementById('dashboard');
    const loginBtn = document.getElementById('login-btn');
    const emailInput = document.getElementById('email-input');
    const loginError = document.getElementById('login-error');
    const logoutBtn = document.getElementById('logout-btn');
    const userEmailDisplay = document.getElementById('user-email-display');

    let authToken = localStorage.getItem('bot_auth_token');
    let userEmail = localStorage.getItem('bot_user_email');

    if (authToken && userEmail) {
        showDashboard();
    }

    loginBtn.addEventListener('click', async () => {
        const email = emailInput.value.trim();
        if (!email) {
            showError("Будь ласка, введіть вашу Google пошту.");
            return;
        }

        try {
            loginBtn.disabled = true;
            loginBtn.innerText = "Авторизація...";
            loginError.classList.add('hidden');

            const resp = await fetch('/api/auth/google', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email })
            });

            const data = await resp.json();

            if (resp.ok && data.success) {
                authToken = data.token;
                userEmail = data.email;
                localStorage.setItem('bot_auth_token', authToken);
                localStorage.setItem('bot_user_email', userEmail);
                showDashboard();
            } else {
                showError(data.error || "Помилка авторизації. Доступ заборонено.");
            }
        } catch (err) {
            showError("Не вдалося з'єднатися із сервером: " + err.message);
        } finally {
            loginBtn.disabled = false;
            loginBtn.innerText = "Увійти з Google";
        }
    });

    logoutBtn.addEventListener('click', () => {
        localStorage.removeItem('bot_auth_token');
        localStorage.removeItem('bot_user_email');
        location.reload();
    });

    function showError(msg) {
        loginError.innerText = msg;
        loginError.classList.remove('hidden');
    }

    function showDashboard() {
        loginModal.classList.add('hidden');
        dashboard.classList.remove('hidden');
        userEmailDisplay.innerText = userEmail;
        startDashboardPolling();
    }

    // Real-time Polling Engine
    function startDashboardPolling() {
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
                logoutBtn.click();
                return;
            }
            const data = await resp.json();

            // Update UI Stats
            const totalUsdt = data.usdt_total !== undefined ? data.usdt_total : data.usdt_balance;
            const freeUsdt = data.usdt_balance !== undefined ? data.usdt_balance : 0;
            const initUsdt = data.initial_capital !== undefined ? data.initial_capital : 10;
            
            document.getElementById('stat-balance').innerText = `$${totalUsdt.toFixed(2)} USDT`;
            const balSubElem = document.getElementById('stat-balance-sub');
            if (balSubElem) {
                balSubElem.innerText = `Вільні кошти: $${freeUsdt.toFixed(2)} | Початкові: $${initUsdt.toFixed(2)}`;
            }

            document.getElementById('stat-symbol-price').innerText = `${data.symbol} $${data.current_price.toFixed(4)}`;
            document.getElementById('stat-timeframe').innerText = `Таймфрейм: ${data.timeframe}`;
            
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
            if (data.llm_enabled) {
                llmElem.innerText = `АКТИВНИЙ (${data.llm_provider})`;
                llmElem.className = 'stat-value text-green';
            } else {
                llmElem.innerText = 'ВИМКНЕНО';
                llmElem.className = 'stat-value text-red';
            }
            if (providerSelect && data.llm_provider) {
                providerSelect.value = data.llm_provider.toLowerCase();
            }

            const symbolSelect = document.getElementById('symbol-select');
            if (symbolSelect && data.symbol) {
                symbolSelect.value = data.symbol;
            }

            // Mode badge
            document.getElementById('mode-badge').innerText = data.mode;

            // Active Position
            const posContainer = document.getElementById('active-position-container');
            if (data.active_position) {
                const pos = data.active_position;
                const pnl = ((data.current_price - pos.entry_price) / pos.entry_price) * 100;
                const pnlClass = pnl >= 0 ? 'text-green' : 'text-red';
                posContainer.innerHTML = `
                    <div style="font-size: 1.1rem; margin-bottom: 8px;">
                        <strong>${data.symbol}</strong> — ${pos.amount.toFixed(4)} монет
                    </div>
                    <div>Ціна входу: $${pos.entry_price.toFixed(4)} | Поточна: $${data.current_price.toFixed(4)}</div>
                    <div class="${pnlClass}" style="font-size: 1.2rem; font-weight: 700; margin-top: 8px;">
                        PnL: ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%
                    </div>
                `;
            } else {
                posContainer.innerHTML = `<div class="empty-state">Немає відкритих угод (Сканування ринку...)</div>`;
            }

            // Controls state
            const toggleActiveBtn = document.getElementById('toggle-active-btn');
            if (data.is_active) {
                toggleActiveBtn.innerText = "▶️ Бот працює";
                toggleActiveBtn.className = "btn btn-success";
            } else {
                toggleActiveBtn.innerText = "🛑 Бот на паузі";
                toggleActiveBtn.className = "btn btn-warning";
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
            const orders = data.order_history || [];

            if (orders.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" class="text-center">Очікування першої угоди...</td></tr>`;
                return;
            }

            tbody.innerHTML = orders.map(ord => {
                const dateStr = new Date(ord.timestamp).toLocaleTimeString();
                const sideClass = ord.side === 'buy' ? 'text-green' : 'text-red';
                return `
                    <tr>
                        <td>${dateStr}</td>
                        <td>${ord.symbol}</td>
                        <td class="${sideClass}"><strong>${ord.side.toUpperCase()}</strong></td>
                        <td>$${ord.price.toFixed(4)}</td>
                        <td>${ord.amount.toFixed(4)}</td>
                        <td><span class="badge ${ord.status === 'closed' ? 'badge-paper' : ''}">${ord.status}</span></td>
                        <td>🟢 Виконано за алгоритмом</td>
                    </tr>
                `;
            }).join('');
        } catch (err) {
            console.error("Orders polling error:", err);
        }
    }

    // Control Handlers
    document.getElementById('toggle-active-btn').addEventListener('click', async () => {
        const btn = document.getElementById('toggle-active-btn');
        const isCurrentlyActive = btn.innerText.includes("працює");
        const action = isCurrentlyActive ? 'pause' : 'resume';

        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
        fetchStatus();
    });

    document.getElementById('toggle-mode-btn').addEventListener('click', async () => {
        if (confirm("Ви дійсно хочете змінити режим торгівлі (Paper / Live)?")) {
            await fetch('/api/control', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: 'toggle_mode' })
            });
            fetchStatus();
        }
    });

    document.getElementById('btn-mode-chill').addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'chill' })
        });
        fetchStatus();
    });

    document.getElementById('btn-mode-hunt').addEventListener('click', async () => {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_trading_mode', mode: 'hunt' })
        });
        fetchStatus();
    });

    document.getElementById('provider-select').addEventListener('change', async (e) => {
        const provider = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_llm_provider', provider })
        });
        fetchStatus();
    });

    document.getElementById('symbol-select').addEventListener('change', async (e) => {
        const symbol = e.target.value;
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'change_symbol', symbol })
        });
        fetchStatus();
    });
});
