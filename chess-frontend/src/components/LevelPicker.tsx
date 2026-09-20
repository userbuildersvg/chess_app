import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import './OpponentLevelPicker.css';

/**
 * A compact level picker: a trigger that says what you have, opening a small
 * list of rows - name, a mono tag, one line each - with the current one
 * marked, and optional locked rows that explain what would unlock them. The
 * opponent level in Play and Learn and the coach lens in Review are the same
 * control; only the rows differ.
 */
export interface LevelOption {
    id: string;
    label: string;
    /** The small mono tag after the name: "about 1500". */
    tag?: string;
    blurb: string;
}

export interface LockedOption {
    id: string;
    label: string;
    blurb: string;
    testId?: string;
}

export function LevelPicker({ value, current, options, locked = [], note, onChange, disabled, ariaLabel }: {
    value: string;
    /** What the trigger says. */
    current: string;
    options: LevelOption[];
    locked?: LockedOption[];
    /** One line at the top of the menu saying what the choice changes. */
    note?: string;
    onChange: (id: string) => void;
    disabled?: boolean;
    ariaLabel: string;
}) {
    const [open, setOpen] = useState(false);
    const root = useRef<HTMLDivElement>(null);
    // The menu is fixed to the viewport rather than to the trigger: two of
    // its homes (Review's Actions list, Play's rail) are scroll boxes that
    // hide overflow, and an absolutely positioned menu was cut at their
    // edge. It opens downward when there is room and upward otherwise, and
    // never taller than the side it opens on.
    const [pos, setPos] = useState<CSSProperties>({});
    useLayoutEffect(() => {
        if (!open || !root.current) return;
        const place = () => {
            const r = root.current!.getBoundingClientRect();
            const below = window.innerHeight - r.bottom - 12;
            const above = r.top - 12;
            const up = below < 320 && above > below;
            const width = Math.min(360, window.innerWidth - 32);
            const left = Math.max(16, Math.min(r.left, window.innerWidth - width - 16));
            setPos({
                position: 'fixed', left, width,
                ...(up ? { bottom: window.innerHeight - r.top + 6 } : { top: r.bottom + 6 }),
                maxHeight: Math.max(200, (up ? above : below)),
            });
        };
        place();
        window.addEventListener('resize', place);
        window.addEventListener('scroll', place, true);
        return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
    }, [open]);

    return (
        <div className={`level-picker ${open ? 'is-open' : ''}`} ref={root}>
            <button
                type="button"
                className="level-picker-trigger"
                aria-haspopup="listbox"
                aria-expanded={open}
                aria-label={ariaLabel}
                disabled={disabled}
                onClick={() => setOpen(o => !o)}
            >
                <span className="level-picker-current">{current}</span>
                <span className="level-picker-chevron" aria-hidden="true">▾</span>
            </button>
            {open && (
                <div className="level-picker-menu" role="listbox" aria-label={ariaLabel} style={pos}>
                    {note && <p className="level-picker-note">{note}</p>}
                    {options.map(p => {
                        const active = p.id === value;
                        return (
                            <button
                                key={p.id}
                                type="button"
                                role="option"
                                aria-selected={active}
                                className={`level-picker-option ${active ? 'is-active' : ''}`}
                                onClick={() => { onChange(p.id); setOpen(false); }}
                            >
                                <span className="level-picker-name">
                                    {p.label}
                                    {p.tag && <span className="level-picker-elo">{p.tag}</span>}
                                </span>
                                <span className="level-picker-blurb">{p.blurb}</span>
                            </button>
                        );
                    })}
                    {locked.map(l => (
                        <div key={l.id} className="level-picker-option is-locked" role="option" aria-selected={false} aria-disabled="true" data-testid={l.testId}>
                            <span className="level-picker-name">{l.label}</span>
                            <span className="level-picker-blurb">{l.blurb}</span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
