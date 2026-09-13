import { useEffect, useRef, useState } from 'react';
import {
    COACH_PRESETS,
    DEFAULT_COACH_BEHAVIOR,
    coachPreview,
    coachStyleSummary,
    coachVoiceLabel,
    readCoachBehavior,
    saveCoachBehavior,
    type CoachBehaviorSettings,
} from '../coachBehavior';
import './CoachStyleSettings.css';

const clamp = (value: number) => Math.max(0, Math.min(10, Math.round(value * 10) / 10));

export function CoachStyleSettings() {
    const [open, setOpen] = useState(false);
    const [settings, setSettings] = useState<CoachBehaviorSettings>(readCoachBehavior);
    const triggerRef = useRef<HTMLButtonElement>(null);
    const closeRef = useRef<HTMLButtonElement>(null);
    const planeRef = useRef<HTMLDivElement>(null);

    const update = (patch: Partial<CoachBehaviorSettings>) => {
        setSettings(current => {
            const next = { ...current, ...patch };
            saveCoachBehavior(next);
            return next;
        });
    };

    const setCoordinates = (bluntness: number, creativity: number) => {
        update({ bluntness: clamp(bluntness), creativity: clamp(creativity), preset: 'custom' });
    };

    const positionFromPointer = (clientX: number, clientY: number) => {
        const rect = planeRef.current?.getBoundingClientRect();
        if (!rect) return;
        setCoordinates(
            ((clientX - rect.left) / rect.width) * 10,
            (1 - ((clientY - rect.top) / rect.height)) * 10,
        );
    };

    const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        positionFromPointer(event.clientX, event.clientY);
    };

    const close = () => {
        setOpen(false);
        window.setTimeout(() => triggerRef.current?.focus(), 0);
    };

    useEffect(() => {
        if (!open) return;
        closeRef.current?.focus();
        const onKey = (event: KeyboardEvent) => {
            if (event.key === 'Escape') close();
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [open]);

    const movePoint = (dx: number, dy: number) => {
        setCoordinates(settings.bluntness + dx, settings.creativity + dy);
    };

    return (
        <>
            <div className="actions-item coach-style-entry">
                <button
                    ref={triggerRef}
                    type="button"
                    className="action-btn coach-style-open"
                    onClick={() => setOpen(true)}
                >
                    Advanced…
                </button>
                <span className="actions-note">
                    <strong>Coach style</strong>
                    <span>{coachStyleSummary(settings)}. Changes delivery, never chess facts.</span>
                </span>
            </div>

            {open && (
                <div className="coach-style-backdrop" onMouseDown={event => {
                    if (event.target === event.currentTarget) close();
                }}>
                    <section
                        className="coach-style-modal"
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="coach-style-title"
                    >
                        <header className="coach-style-head">
                            <div>
                                <p className="coach-style-kicker">Advanced coach settings</p>
                                <h2 id="coach-style-title">Behavioral space</h2>
                                <p>Shape how the coach speaks. Engine analysis and move grades stay unchanged.</p>
                            </div>
                            <button ref={closeRef} type="button" className="coach-style-close" onClick={close} aria-label="Close coach style settings">×</button>
                        </header>

                        <div className="coach-style-values" aria-live="polite">
                            <span>Directness: <strong>{settings.bluntness.toFixed(1)} / 10</strong></span>
                            <span>Creativity: <strong>{settings.creativity.toFixed(1)} / 10</strong></span>
                            <span>Voice: <strong>{coachVoiceLabel(settings)}</strong></span>
                        </div>

                        <figure className="coach-style-preview" data-testid="coach-style-preview">
                            <figcaption>Coach preview <span>same position, same fact, your settings</span></figcaption>
                            <blockquote>&ldquo;{coachPreview(settings)}&rdquo;</blockquote>
                        </figure>

                        <div className="coach-style-main">
                            <div className="coach-plane-wrap">
                                <span className="coach-axis-y coach-axis-top">Creative / Expressive</span>
                                <div
                                    ref={planeRef}
                                    className="coach-plane"
                                    onPointerDown={onPointerDown}
                                    onPointerMove={event => {
                                        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                                            positionFromPointer(event.clientX, event.clientY);
                                        }
                                    }}
                                >
                                    <span className="coach-plane-glow" />
                                    <button
                                        type="button"
                                        className="coach-point"
                                        aria-label={`Directness ${settings.bluntness.toFixed(1)}, creativity ${settings.creativity.toFixed(1)}`}
                                        style={{ left: `${settings.bluntness * 10}%`, bottom: `${settings.creativity * 10}%` }}
                                        onKeyDown={event => {
                                            const step = event.shiftKey ? 1 : 0.1;
                                            if (event.key === 'ArrowLeft') movePoint(-step, 0);
                                            else if (event.key === 'ArrowRight') movePoint(step, 0);
                                            else if (event.key === 'ArrowDown') movePoint(0, -step);
                                            else if (event.key === 'ArrowUp') movePoint(0, step);
                                            else return;
                                            event.preventDefault();
                                        }}
                                    />
                                </div>
                                <div className="coach-axis-x"><span>Gentle</span><span>Blunt / Direct</span></div>
                                <span className="coach-axis-y coach-axis-bottom">Stable / Literal</span>
                            </div>

                            <div className="coach-sliders">
                                <label>
                                    <span><strong>Directness</strong><output>{settings.bluntness.toFixed(1)}</output></span>
                                    <input type="range" min="0" max="10" step="0.1" value={settings.bluntness}
                                        onChange={event => setCoordinates(Number(event.target.value), settings.creativity)} />
                                    <small>Gentle and encouraging → clear and tactically blunt</small>
                                </label>
                                <label>
                                    <span><strong>Creativity</strong><output>{settings.creativity.toFixed(1)}</output></span>
                                    <input type="range" min="0" max="10" step="0.1" value={settings.creativity}
                                        onChange={event => setCoordinates(settings.bluntness, Number(event.target.value))} />
                                    <small>Stable and literal → vivid and expressive</small>
                                </label>
                                <div className="coach-presets" aria-label="Coach style presets">
                                    <span>Presets</span>
                                    <div>
                                        {COACH_PRESETS.map(preset => (
                                            <button key={preset.id} type="button"
                                                className={settings.preset === preset.id ? 'is-active' : ''}
                                                onClick={() => update({
                                                    bluntness: preset.bluntness,
                                                    creativity: preset.creativity,
                                                    preset: preset.id,
                                                })}>
                                                {preset.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <footer className="coach-style-footer">
                            <button type="button" className="action-btn" onClick={() => update(DEFAULT_COACH_BEHAVIOR)}>Reset to balanced</button>
                            <button type="button" className="action-btn coach-style-done" onClick={close}>Done</button>
                        </footer>
                    </section>
                </div>
            )}
        </>
    );
}
