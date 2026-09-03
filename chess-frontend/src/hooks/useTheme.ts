import { useCallback, useEffect, useState } from 'react';

/**
 * Theme preference.
 *
 * 'system' is a real, persistable state rather than the absence of a choice:
 * someone who wants the app to follow their OS should be able to say so and
 * have it stick, including after they have previously pinned a theme. It is
 * also the default, so a first visit matches the rest of the machine.
 *
 * The DOM contract is the one styles/obsidian.css is written against:
 *   - 'dark'  -> html[data-theme="dark"]
 *   - 'light' -> html[data-theme="light"]
 *   - 'system'-> no attribute at all, leaving the prefers-color-scheme media
 *                query to decide.
 * The attribute is removed rather than set to "system" because the CSS
 * guards its OS branch on :not([data-theme=...]) - an attribute with any
 * value would suppress it.
 */
export type ThemePreference = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'zugzwang-theme';

function readStoredPreference(): ThemePreference {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored === 'light' || stored === 'dark' || stored === 'system') {
            return stored;
        }
    } catch {
        // localStorage unavailable (private mode, blocked site data) - the
        // app must still render, so fall through to the default.
    }
    return 'system';
}

function applyPreference(preference: ThemePreference) {
    const root = document.documentElement;
    if (preference === 'system') {
        root.removeAttribute('data-theme');
    } else {
        root.setAttribute('data-theme', preference);
    }
}

/**
 * Resolves a preference to the theme actually being displayed, which is what
 * a toggle needs in order to label itself ("Switch to light") and what any
 * canvas- or SVG-drawn colour would need to read.
 */
function resolve(preference: ThemePreference): 'light' | 'dark' {
    if (preference !== 'system') return preference;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function useTheme() {
    const [preference, setPreference] = useState<ThemePreference>(readStoredPreference);
    const [resolved, setResolved] = useState<'light' | 'dark'>(() => resolve(readStoredPreference()));

    // Apply on mount as well as on change: the preference is read from
    // storage during the first render, so the attribute has to catch up.
    useEffect(() => {
        applyPreference(preference);
        setResolved(resolve(preference));
        try {
            localStorage.setItem(STORAGE_KEY, preference);
        } catch {
            // Persisting is a convenience; failing to persist must not break
            // the switch the user just made.
        }
    }, [preference]);

    // While following the system, track it live - someone whose OS flips to
    // dark at sunset should see the app follow without a reload.
    useEffect(() => {
        if (preference !== 'system') return;
        const query = window.matchMedia('(prefers-color-scheme: light)');
        const onChange = () => setResolved(query.matches ? 'light' : 'dark');
        query.addEventListener('change', onChange);
        return () => query.removeEventListener('change', onChange);
    }, [preference]);

    // Cycling three states through one control is a guessing game, so the
    // toggle only ever moves between light and dark. It resolves 'system'
    // first, meaning the first press flips away from whatever is on screen
    // rather than jumping to an unrelated theme.
    const toggle = useCallback(() => {
        setPreference(resolve(preference) === 'dark' ? 'light' : 'dark');
    }, [preference]);

    return { preference, setPreference, resolved, toggle };
}
