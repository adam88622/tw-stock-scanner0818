// ===== 全球行情跑馬燈 (diff/reuse, no full re-render) =====
function loadTicker() {
    fetch('/api/quotes').then(function (r) { return r.json(); }).then(function (data) {
        var el = document.getElementById('ticker-inner');
        if (!el || !data.length) return;

        // Build a map of label -> existing item DOM (use only first half; second half is the duplicate clone)
        var existingItems = el.querySelectorAll('.ticker-item');
        var firstCount = existingItems.length / 2;
        var existingByLabel = {};
        if (firstCount > 0 && firstCount === Math.floor(firstCount)) {
            for (var i = 0; i < firstCount; i++) {
                var node = existingItems[i];
                var labelEl = node.querySelector('.ticker-label');
                if (labelEl) existingByLabel[labelEl.textContent] = node;
            }
        }

        // Same set of labels as before? -> diff update only
        var labelsMatch = (Object.keys(existingByLabel).length === data.length) &&
            data.every(function (q) { return existingByLabel.hasOwnProperty(q.label); });

        if (labelsMatch) {
            data.forEach(function (q) {
                var node = existingByLabel[q.label];
                var priceEl = node.querySelector('.ticker-price');
                var chgEl = node.querySelector('.ticker-chg');
                var newPrice = q.price.toLocaleString();
                var sign = q.pct > 0 ? '▲' : q.pct < 0 ? '▼' : '';
                var newChg = sign + Math.abs(q.pct).toFixed(2) + '%';
                var newCls = q.pct > 0 ? 'ticker-up' : q.pct < 0 ? 'ticker-dn' : 'ticker-flat';
                if (priceEl && priceEl.textContent !== newPrice) priceEl.textContent = newPrice;
                if (chgEl) {
                    if (chgEl.textContent !== newChg) chgEl.textContent = newChg;
                    if (!chgEl.classList.contains(newCls)) {
                        chgEl.classList.remove('ticker-up', 'ticker-dn', 'ticker-flat');
                        chgEl.classList.add(newCls);
                    }
                }
            });
            // Mirror updates to the duplicated half if structure intact
            for (var j = 0; j < firstCount; j++) {
                var src = existingItems[j];
                var dup = existingItems[j + firstCount];
                if (!src || !dup) continue;
                var dupPrice = dup.querySelector('.ticker-price');
                var dupChg = dup.querySelector('.ticker-chg');
                var srcPrice = src.querySelector('.ticker-price');
                var srcChg = src.querySelector('.ticker-chg');
                if (dupPrice && srcPrice && dupPrice.textContent !== srcPrice.textContent) {
                    dupPrice.textContent = srcPrice.textContent;
                }
                if (dupChg && srcChg) {
                    if (dupChg.textContent !== srcChg.textContent) dupChg.textContent = srcChg.textContent;
                    dupChg.className = srcChg.className;
                }
            }
            return;
        }

        // Structure changed: full re-render (animation will restart, acceptable rare case)
        var html = '';
        data.forEach(function (q) {
            var cls = q.pct > 0 ? 'ticker-up' : q.pct < 0 ? 'ticker-dn' : 'ticker-flat';
            var sign = q.pct > 0 ? '▲' : q.pct < 0 ? '▼' : '';
            html += '<div class="ticker-item">' +
                '<span class="ticker-label">' + q.label + '</span>' +
                '<span class="ticker-price">' + q.price.toLocaleString() + '</span>' +
                '<span class="ticker-chg ' + cls + '">' + sign + Math.abs(q.pct).toFixed(2) + '%</span>' +
                '</div>';
        });
        el.innerHTML = html + html;
    }).catch(function () { });
}
document.addEventListener('DOMContentLoaded', loadTicker);
setInterval(loadTicker, 300000); // 5 min

// ===== Mobile: wrap .filter-bar content into a collapsible accordion =====
document.addEventListener('DOMContentLoaded', function () {
    var bars = document.querySelectorAll('.filter-bar');
    bars.forEach(function (bar) {
        // Skip if already wrapped
        if (bar.querySelector(':scope > .filter-bar-body')) return;
        // Move all existing children into a wrapper
        var body = document.createElement('div');
        body.className = 'filter-bar-body';
        while (bar.firstChild) body.appendChild(bar.firstChild);

        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'filter-bar-toggle';
        toggle.setAttribute('aria-expanded', 'true');
        toggle.textContent = '篩選條件';

        bar.appendChild(toggle);
        bar.appendChild(body);

        // Default collapsed on mobile
        if (window.innerWidth <= 768) {
            bar.classList.add('collapsed');
            toggle.setAttribute('aria-expanded', 'false');
        }

        toggle.addEventListener('click', function () {
            bar.classList.toggle('collapsed');
            toggle.setAttribute('aria-expanded', bar.classList.contains('collapsed') ? 'false' : 'true');
        });
    });
});

// ===== 表格排序功能 (with visual indicators + data-no-sort skip) =====
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table.data-table').forEach(function (table) {
        var ths = table.querySelectorAll('thead th');
        ths.forEach(function (th, colIdx) {
            // Skip non-sortable columns flagged via data-no-sort
            if (th.hasAttribute('data-no-sort')) {
                th.classList.add('no-sort');
                return;
            }
            // Heuristic: skip "#" and obvious action columns when not flagged
            var label = (th.textContent || '').trim();
            if (label === '#' || label === '操作') {
                th.classList.add('no-sort');
                return;
            }
            th.classList.add('sortable');
            // Inject sort indicator if not already present
            if (!th.querySelector('.sort-indicator')) {
                var ind = document.createElement('span');
                ind.className = 'sort-indicator';
                ind.setAttribute('aria-hidden', 'true');
                th.appendChild(ind);
            }
            th.style.cursor = 'pointer';
            th.addEventListener('click', function () {
                sortTable(table, colIdx, th);
            });
        });
    });
});

function sortTable(table, colIdx, headerEl) {
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.from(tbody.querySelectorAll('tr'));
    var prevCol = parseInt(table.dataset.sortCol, 10);
    var currentDir;
    if (prevCol === colIdx) {
        currentDir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        currentDir = 'asc';
    }
    table.dataset.sortDir = currentDir;
    table.dataset.sortCol = String(colIdx);

    rows.sort(function (a, b) {
        var aCell = a.cells[colIdx];
        var bCell = b.cells[colIdx];
        if (!aCell || !bCell) return 0;
        var aText = aCell.textContent.trim();
        var bText = bCell.textContent.trim();
        var aNum = parseFloat(aText.replace(/[,%+]/g, ''));
        var bNum = parseFloat(bText.replace(/[,%+]/g, ''));
        if (!isNaN(aNum) && !isNaN(bNum)) {
            return currentDir === 'asc' ? aNum - bNum : bNum - aNum;
        }
        return currentDir === 'asc'
            ? aText.localeCompare(bText, 'zh-TW')
            : bText.localeCompare(aText, 'zh-TW');
    });

    rows.forEach(function (row) { tbody.appendChild(row); });

    // Update sort class on headers
    table.querySelectorAll('thead th').forEach(function (h) {
        h.classList.remove('sort-asc', 'sort-desc');
    });
    if (headerEl) headerEl.classList.add(currentDir === 'asc' ? 'sort-asc' : 'sort-desc');
}
