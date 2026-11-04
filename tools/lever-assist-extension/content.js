// Same script as the bookmarklet, loaded on every Lever page so a confirmation opens the next job by itself.
const s = document.createElement('script');
s.src = 'http://127.0.0.1:8799/assist.js?auto=1&' + Date.now();
document.head.append(s);
