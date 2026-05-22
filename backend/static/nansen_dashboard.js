/**
 * Nansen Intelligence Monitor — Dashboard Logic
 */

async function fetchStats() {
    try {
        const response = await fetch('/api/nansen/stats');
        if (!response.ok) throw new Error('Stats fetch failed');
        const data = await response.json();
        updateUI(data);
    } catch (err) {
        console.error('Failed to update dashboard:', err);
    }
}

function updateUI(data) {
    // Update Monthly Gauge
    const gauge = document.getElementById('month-gauge');
    const percentText = document.getElementById('month-percent');
    const usageText = document.getElementById('month-usage');
    
    const percent = data.percent_month;
    const offset = 283 - (283 * percent) / 100;
    
    gauge.style.strokeDashoffset = offset;
    percentText.textContent = `${Math.round(percent)}%`;
    usageText.textContent = `$${data.month_usage.toLocaleString()}`;
    
    // Update Gauge Color based on usage
    if (percent > 80) {
        gauge.style.stroke = '#ef4444'; // Red
        document.getElementById('governor-status').textContent = 'CRITICAL';
        document.getElementById('governor-status').className = 'stat-val status-critical';
    } else if (percent > 50) {
        gauge.style.stroke = '#f59e0b'; // Amber
        document.getElementById('governor-status').textContent = 'WARNING';
        document.getElementById('governor-status').className = 'stat-val status-warning';
    } else {
        gauge.style.stroke = '#00f2ff'; // Cyan
        document.getElementById('governor-status').textContent = 'OPTIMAL';
        document.getElementById('governor-status').className = 'stat-val status-ok';
    }
}

// Polling interval
setInterval(fetchStats, 5000);

// Initial fetch
fetchStats();

// Sample: Inject some live flows for "WOW" effect
function addSampleFlow() {
    const container = document.getElementById('flow-container');
    const assets = ['SOL', 'ETH', 'BTC', 'JUP', 'PYTH'];
    const insts = ['BLACKROCK', 'VANGUARD', 'CITADEL', 'FIDELITY'];
    
    const isBuy = Math.random() > 0.4;
    const asset = assets[Math.floor(Math.random() * assets.length)];
    const inst = insts[Math.floor(Math.random() * insts.length)];
    const amt = (Math.random() * 50 + 10).toFixed(1);
    const time = new Date().toLocaleTimeString('en-GB', { hour12: false });

    const html = `
        <div class="flow-item ${isBuy ? 'buy' : 'sell'} new-item">
            <div class="flow-time">${time}</div>
            <div class="flow-body">
                <span class="inst">${inst}</span>
                <span class="side">${isBuy ? 'BUY' : 'SELL'}</span>
                <span class="asset">${asset}</span>
                <span class="amt">${isBuy ? '' : '-'}$${amt}M</span>
            </div>
        </div>
    `;
    
    container.insertAdjacentHTML('afterbegin', html);
    if (container.children.length > 8) container.lastElementChild.remove();
}

setInterval(addSampleFlow, 8000);
addSampleFlow();
