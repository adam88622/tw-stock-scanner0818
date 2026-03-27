// ===== 全球行情跑馬燈 =====
function loadTicker() {
    fetch('/api/quotes').then(r=>r.json()).then(data=>{
        const el = document.getElementById('ticker-inner');
        if (!el || !data.length) return;
        let html = '';
        data.forEach(q => {
            const cls = q.pct > 0 ? 'ticker-up' : q.pct < 0 ? 'ticker-dn' : 'ticker-flat';
            const sign = q.pct > 0 ? '\u25B2' : q.pct < 0 ? '\u25BC' : '';
            html += `<div class="ticker-item">
                <span class="ticker-label">${q.label}</span>
                <span class="ticker-price">${q.price.toLocaleString()}</span>
                <span class="ticker-chg ${cls}">${sign}${Math.abs(q.pct).toFixed(2)}%</span>
            </div>`;
        });
        // Duplicate for seamless loop
        el.innerHTML = html + html;
    }).catch(()=>{});
}
document.addEventListener('DOMContentLoaded', loadTicker);
setInterval(loadTicker, 60000);

// ===== 表格排序功能 =====
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.data-table thead th').forEach(function (th, colIdx) {
        th.style.cursor = 'pointer';
        th.addEventListener('click', function () {
            sortTable(th.closest('table'), colIdx);
        });
    });
});

function sortTable(table, colIdx) {
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const currentDir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    table.dataset.sortDir = currentDir;
    table.dataset.sortCol = colIdx;

    rows.sort(function (a, b) {
        let aText = a.cells[colIdx].textContent.trim();
        let bText = b.cells[colIdx].textContent.trim();

        // 嘗試數字排序
        let aNum = parseFloat(aText.replace(/[,%+]/g, ''));
        let bNum = parseFloat(bText.replace(/[,%+]/g, ''));

        if (!isNaN(aNum) && !isNaN(bNum)) {
            return currentDir === 'asc' ? aNum - bNum : bNum - aNum;
        }
        // 文字排序
        return currentDir === 'asc'
            ? aText.localeCompare(bText, 'zh-TW')
            : bText.localeCompare(aText, 'zh-TW');
    });

    rows.forEach(function (row) {
        tbody.appendChild(row);
    });

    // 更新排序指示
    table.querySelectorAll('thead th').forEach(function (h) {
        h.classList.remove('sort-asc', 'sort-desc');
    });
    table.querySelectorAll('thead th')[colIdx].classList.add(
        currentDir === 'asc' ? 'sort-asc' : 'sort-desc'
    );
}
