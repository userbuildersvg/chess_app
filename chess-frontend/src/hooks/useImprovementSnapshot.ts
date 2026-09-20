import { useEffect, useState } from 'react';
import { authService } from '../services/authService';
import { learningService } from '../services/learningService';
import { profileService } from '../services/profileService';
import type { Profile } from '../services/profileService';
import type { Correction } from '../types/learning';

/**
 * What Zugzwang currently remembers about this person, read once on mount:
 * whether they are signed in, their improvement profile (signed in only -
 * a guest's read is a 401 the browser logs as an error), and the latest
 * saved lesson. Best-effort: a failed read leaves the field null, never
 * throws, never invents a number.
 */
export interface ImprovementSnapshot {
    loaded: boolean;
    signedIn: boolean;
    username: string | null;
    profile: Profile | null;
    latestLesson: Correction | null;
}

export function useImprovementSnapshot(): ImprovementSnapshot {
    const [snap, setSnap] = useState<ImprovementSnapshot>({ loaded: false, signedIn: false, username: null, profile: null, latestLesson: null });
    useEffect(() => {
        let live = true;
        (async () => {
            let signedIn = false;
            let username: string | null = null;
            try { const me = await authService.me(); signedIn = me.signed_in; username = me.username ?? null; } catch { /* treated as a guest */ }
            const [profile, lessons] = await Promise.all([
                signedIn ? profileService.profile().catch(() => null) : Promise.resolve(null),
                learningService.corrections().catch(() => null),
            ]);
            if (!live) return;
            const latestLesson = lessons ? [...lessons.corrections].sort((a, b) => b.last_seen_at - a.last_seen_at)[0] ?? null : null;
            setSnap({ loaded: true, signedIn, username, profile, latestLesson });
        })();
        return () => { live = false; };
    }, []);
    return snap;
}
