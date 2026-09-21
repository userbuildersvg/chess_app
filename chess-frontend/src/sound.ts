import { useCallback, useEffect, useRef, useState } from 'react';
import type { BoardStatus } from './boardState';

/**
 * Board sounds, generated in code.
 *
 * Four short WebAudio tones - move, capture, check, game over - synthesised
 * on the fly rather than shipped as files, so there is nothing to license,
 * credit or download: the app makes its own noise. They are quiet on purpose
 * (a click under speech level) and the whole thing is one switch, shared by
 * all three boards the way the board size is (useBoardScale), because two
 * boards in one app that sound different read as two apps.
 *
 * Nothing plays until the person has clicked or pressed a key somewhere on
 * the page: browsers refuse audio before a gesture, and a page that makes a
 * sound as it loads is a page people close. The first position a board shows
 * is never sounded either, so a reload, a restored session or a review
 * arriving from Play is silent - only a change of position is a move.
 */

const KEY = 'zugzwang-sound';
const CHANGED = 'zugzwang-sound-changed';

export type SoundKind = 'move' | 'capture' | 'check' | 'end';

function read(): boolean {
    try {
        return localStorage.getItem(KEY) !== 'off';
    } catch {
        return true;
    }
}

/** The switch, as one preference shared by every board. On by default. */
export function useSoundPref(): [boolean, (on: boolean) => void] {
    const [on, setOn] = useState(read);
    useEffect(() => {
        const sync = () => setOn(read());
        window.addEventListener(CHANGED, sync);
        window.addEventListener('storage', sync);
        return () => {
            window.removeEventListener(CHANGED, sync);
            window.removeEventListener('storage', sync);
        };
    }, []);
    const choose = useCallback((next: boolean) => {
        try { localStorage.setItem(KEY, next ? 'on' : 'off'); } catch { /* session only */ }
        setOn(next);
        window.dispatchEvent(new Event(CHANGED));
    }, []);
    return [on, choose];
}

// --- the tones -------------------------------------------------------------

let unlocked = false;
let ctx: AudioContext | null = null;

if (typeof document !== 'undefined') {
    const unlock = () => { unlocked = true; };
    document.addEventListener('pointerdown', unlock, { capture: true, once: true });
    document.addEventListener('keydown', unlock, { capture: true, once: true });
}

function context(): AudioContext | null {
    if (!unlocked || typeof AudioContext === 'undefined') return null;
    ctx ??= new AudioContext();
    if (ctx.state === 'suspended') void ctx.resume();
    return ctx;
}

/** One decaying note. `type` triangle reads as wood, sine as a bell. */
function note(ac: AudioContext, at: number, hz: number, dur: number, gain: number, type: OscillatorType) {
    const osc = ac.createOscillator();
    const env = ac.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(hz, at);
    env.gain.setValueAtTime(0.0001, at);
    env.gain.exponentialRampToValueAtTime(gain, at + 0.004);
    env.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    osc.connect(env).connect(ac.destination);
    osc.start(at);
    osc.stop(at + dur + 0.02);
}

export function playSound(kind: SoundKind): void {
    if (!read()) return;
    const ac = context();
    if (!ac) return;
    const t = ac.currentTime;
    switch (kind) {
        case 'move':      // a soft wooden click
            note(ac, t, 190, 0.07, 0.12, 'triangle');
            break;
        case 'capture':   // lower, twice
            note(ac, t, 150, 0.07, 0.14, 'triangle');
            note(ac, t + 0.07, 120, 0.09, 0.12, 'triangle');
            break;
        case 'check':     // a gentle chime
            note(ac, t, 880, 0.3, 0.05, 'sine');
            note(ac, t, 1320, 0.22, 0.02, 'sine');
            break;
        case 'end':       // two notes, resolved
            note(ac, t, 523, 0.18, 0.06, 'sine');
            note(ac, t + 0.16, 784, 0.32, 0.06, 'sine');
            break;
    }
}

// --- the hook --------------------------------------------------------------

/** Pieces on the board, from the placement field of a FEN. */
const pieceCount = (fen: string): number => (fen.split(' ')[0].match(/[prnbqk]/gi) ?? []).length;

/**
 * Sounds the change from one position to the next. Give it the board's FEN
 * and its status; it plays when the FEN changes and says nothing about the
 * first one it sees. Capture is read off the FEN (fewer pieces than before),
 * check and the endings off the status, so no mode has to know the rule.
 */
export function useMoveSounds(fen: string | null | undefined, status: BoardStatus): void {
    const last = useRef<string | null | undefined>(fen);
    useEffect(() => {
        const prev = last.current;
        last.current = fen;
        if (!fen || !prev || fen === prev) return;
        if (status.gameOver) playSound('end');
        else if (status.inCheck) playSound('check');
        else if (pieceCount(fen) < pieceCount(prev)) playSound('capture');
        else playSound('move');
    }, [fen, status]);
}
