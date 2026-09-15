// ==UserScript==
// @name         noVNC Paste for CloudServer (v0.5 shift-safe)
// @namespace    http://tampermonkey.net/
// @version      0.5
// @description  Pastes text into noVNC via RFB; holds Shift for shifted characters (fixed && _ > quotes)
// @author       Chester Enright (adapted for CloudServer)
// @match        https://www.cloudserver.net/*
// @match        http://www.cloudserver.net/*
// @match        https://cloudserver.net/*
// @match        http://cloudserver.net/*
// @include      */novnc/*
// @include      */vnc*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    const delay = 30; // ms between characters (was 8 — overlapping release timers dropped/garbled chars)

    const SHIFT = 0xFFE1; // Left Shift keysym
    const SPECIAL = { '\n': 0xFF0D, '\t': 0xFF09 }; // Enter, Tab
    // Characters that require Shift on a US layout -> their keysyms
    const SHIFTED = {
        '~': 0x60, '!': 0x21, '@': 0x40, '#': 0x23, '$': 0x24, '%': 0x25,
        '^': 0x5E, '&': 0x26, '*': 0x2A, '(': 0x28, ')': 0x29, '_': 0x5F,
        '+': 0x2B, '{': 0x7B, '}': 0x7D, ':': 0x3A, '"': 0x22,
        '<': 0x3C, '>': 0x3E, '?': 0x3F, '|': 0x7C
    };

    function sendCharViaRFB(ch) {
        if (!window.rfb || !window.rfb.sendKey) {
            console.warn('noVNC Paste: rfb not ready');
            return;
        }
        let ks = null;
        let needShift = false;

        if (SPECIAL[ch] !== undefined) {
            ks = SPECIAL[ch];
        } else if (/[a-z0-9 ]/.test(ch)) {
            ks = ch.charCodeAt(0);                  // unshifted keysyms
        } else if (SHIFTED[ch] !== undefined) {
            ks = SHIFTED[ch]; needShift = true;     // & _ > | ( ) etc.
        } else if (/[A-Z]/.test(ch)) {
            ks = ch.charCodeAt(0); needShift = true;// uppercase = shifted letters
        } else {
            ks = ch.charCodeAt(0);                  // - = [ ] ; ' , . / `
        }

        if (needShift) window.rfb.sendKey(SHIFT, true);
        window.rfb.sendKey(ks, true);
        window.rfb.sendKey(ks, false);              // immediate release — no timer race
        if (needShift) window.rfb.sendKey(SHIFT, false);
    }

    window.sendStringViaRFB = function (text) {
        if (!window.rfb) {
            console.warn('noVNC Paste: rfb not available');
            return;
        }
        text.split('').forEach((ch, i) => {
            setTimeout(() => sendCharViaRFB(ch), delay * i);
        });
    };

    function addPasteButton() {
        if (document.getElementById('novnc-paste-btn')) return;

        const btn = document.createElement('input');
        btn.type = 'button';
        btn.id = 'novnc-paste-btn';
        btn.value = '📋 Paste v0.5';
        btn.style.cssText = 'margin-left:10px;';

        const bar = document.getElementById('noVNC_status_bar');
        if (!bar) return;

        bar.querySelector('td[width="1%"] div').appendChild(btn);

        btn.onclick = function () {
            navigator.clipboard.readText()
                .then(text => {
                    if (!text) {
                        console.warn('noVNC Paste: clipboard is empty');
                        return;
                    }
                    console.log('noVNC Paste: sending', text.length, 'chars');
                    window.sendStringViaRFB(text);
                })
                .catch(() => {
                    const fallback = prompt('Paste text here and press OK:', '');
                    if (fallback) window.sendStringViaRFB(fallback);
                });
        };
    }

    function waitForRFB() {
        if (window.rfb) {
            addPasteButton();
            console.log('noVNC Paste v0.5: button added, rfb ready');
            return;
        }
        setTimeout(waitForRFB, 200);
    }

    waitForRFB();
})();