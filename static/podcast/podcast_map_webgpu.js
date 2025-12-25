function logStatus(msg, isError = false) {
    console.log(msg);
    const loadingEl = document.getElementById('loading');
    if (loadingEl) {
        const line = document.createElement('div');
        line.textContent = msg;
        line.style.fontSize = '14px';
        line.style.marginTop = '5px';
        if (isError) line.style.color = '#ff4444';
        loadingEl.appendChild(line);
    }
}

console.log("Module loaded");
logStatus("DEBUG: Module loaded successfully.");

(async () => {
    logStatus("DEBUG: IIFE started.");
})();