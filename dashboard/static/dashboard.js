/**
 * Aethelark Dashboard Client Controller
 * Secure WebSocket, Authenticated Encrypt-then-MAC, and Cyber-Telemetry UI
 */
'use strict';

function esc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const Aethelark = (function () {
  let token = localStorage.getItem('aethelark_token') || '';
  let secret = localStorage.getItem('aethelark_secret') || '';
  let ws = null;
  let audioStream = null;
  let audioContext = null;
  let audioProcessor = null;

  function showToast(msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
  }

  function deriveKeys(rootSecret) {
    if (typeof CryptoJS === 'undefined' || !rootSecret) return null;
    const cipherKey = CryptoJS.HmacSHA256('aethelark-cipher-v2', rootSecret);
    const macKey = CryptoJS.HmacSHA256('aethelark-mac-v2', rootSecret);
    return { cipherKey, macKey };
  }

  function encryptMessage(plaintext, rootSecret) {
    if (typeof CryptoJS === 'undefined' || !rootSecret) return null;
    const keys = deriveKeys(rootSecret);
    const iv = CryptoJS.lib.WordArray.random(16);
    const encrypted = CryptoJS.AES.encrypt(plaintext, keys.cipherKey, {
      iv: iv,
      mode: CryptoJS.mode.CBC,
      padding: CryptoJS.pad.Pkcs7
    });

    const ivHex = iv.toString(CryptoJS.enc.Hex);
    const ctHex = encrypted.ciphertext.toString(CryptoJS.enc.Hex);
    const payload = CryptoJS.enc.Hex.parse(ivHex + ctHex);
    const mac = CryptoJS.HmacSHA256(payload, keys.macKey);
    const combined = CryptoJS.enc.Hex.parse(ivHex + ctHex + mac.toString(CryptoJS.enc.Hex));
    return CryptoJS.enc.Base64.stringify(combined);
  }

  async function loginWithPin(pin) {
    const cleanPin = pin.toUpperCase().replace(/[^A-HJ-KM-NP-Z2-9]/g, '');
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: cleanPin })
    });
    if (!res.ok) {
      throw new Error((await res.json()).detail || 'Login failed');
    }
    const data = await res.json();
    token = data.token;
    localStorage.setItem('aethelark_token', token);
    return data;
  }

  async function checkDeviceLogin() {
    const deviceTok = localStorage.getItem('aethelark_device_token');
    if (!deviceTok) return false;
    try {
      const res = await fetch('/api/device-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_token: deviceTok })
      });
      if (res.ok) {
        const data = await res.json();
        token = data.token;
        localStorage.setItem('aethelark_token', token);
        return true;
      }
    } catch (e) {
      console.warn('Device login attempt failed:', e);
    }
    return false;
  }

  async function sendCommand(text) {
    if (!token) return;
    let payload = { text: text };
    if (secret) {
      const enc = encryptMessage(text, secret);
      if (enc) {
        payload = { enc: enc };
      }
    }
    const res = await fetch('/api/command', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      throw new Error('Failed to send command');
    }
    showToast('Command executed');
  }

  async function wakeAgent() {
    if (!token) return;
    const res = await fetch('/api/wake', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    });
    const data = await res.json();
    showToast(data.delivered ? 'Wake signal delivered ⚡' : 'Agent offline');
  }

  function connectWebSocket() {
    if (ws) return;
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws?token=${encodeURIComponent(token)}`);
    ws.onmessage = function (event) {
      try {
        const msg = JSON.parse(event.data);
        appendFeedMessage(msg);
      } catch (e) {}
    };
    ws.onclose = function () {
      ws = null;
      setTimeout(connectWebSocket, 3000);
    };
  }

  function appendFeedMessage(msg) {
    const feed = document.getElementById('feed');
    if (!feed) return;
    const div = document.createElement('div');
    div.className = `feed-item glass-panel ${esc(msg.type || 'sys')}`;
    div.innerHTML = `<span class="time">${new Date().toLocaleTimeString()}</span> <span class="content">${esc(msg.text || JSON.stringify(msg))}</span>`;
    feed.appendChild(div);
    feed.scrollTop = feed.scrollHeight;
  }

  return {
    loginWithPin,
    checkDeviceLogin,
    sendCommand,
    wakeAgent,
    connectWebSocket,
    showToast
  };
})();
