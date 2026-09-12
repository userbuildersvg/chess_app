import React from 'react';
import type { BoardEnd } from '../boardState';
import './BoardEndState.css';

/**
 * A second, quieter action under the reset - Play's "Review this game".
 * Optional because Learn and Review have no such next step: a sandbox line
 * is not a game, and a review is already where a game goes.
 */
export interface BoardEndSecondary {
    label: string;
    /** One line under the label saying what it does. Kept short on purpose. */
    hint?: string;
    onClick: () => void;
    /** While the handoff request is in flight; the button says so and waits. */
    busy?: boolean;
    busyLabel?: string;
    /** What went wrong, under the button, if it did. */
    error?: string | null;
}

interface BoardEndStateProps {
    /** Null while the game is live - the overlay renders nothing. */
    end: BoardEnd | null;
    /** The mode's OWN reset action. This component never resets anything itself. */
    onReset: () => void;
    /** ...and the mode's own word for it, so nothing gets a second name. */
    resetLabel: string;
    secondary?: BoardEndSecondary;
}

/**
 * The layer that goes over a board whose game has ended.
 *
 * It belongs to the board wrapper (which is `position: relative` in all
 * three modes) and not to the page, for the reason trap 11 in CLAUDE.md
 * records: anything sized against the page rather than the board column
 * ends up over the coaching panel at some viewport. `inset: 0` inside the
 * frame cannot reach anything the board does not already cover.
 *
 * The final position stays readable through it. That is the whole design:
 * the tint says the game is over, the word says which ending, and the
 * board underneath is still the thing you came to look at.
 */
export const BoardEndState: React.FC<BoardEndStateProps> = ({ end, onReset, resetLabel, secondary }) => {
    if (!end) {
        return null;
    }
    return (
        <div
            className={`board-endstate is-${end.kind}`}
            role="alertdialog"
            aria-label={`${end.headline}. ${end.detail}`}
        >
            <div className="board-endstate-card">
                <p className="board-endstate-headline">{end.headline}</p>
                <p className="board-endstate-detail">{end.detail}</p>
                <button type="button" className="board-endstate-reset" onClick={onReset}>
                    {resetLabel}
                </button>
                {secondary && (
                    <>
                        <button
                            type="button"
                            className="board-endstate-secondary"
                            onClick={secondary.onClick}
                            disabled={secondary.busy}
                            aria-busy={secondary.busy || undefined}
                            title={secondary.hint}
                        >
                            {secondary.busy ? (secondary.busyLabel ?? secondary.label) : secondary.label}
                        </button>
                        {secondary.hint && !secondary.error && (
                            <p className="board-endstate-hint">{secondary.hint}</p>
                        )}
                        {secondary.error && (
                            <p className="board-endstate-error" role="alert">{secondary.error}</p>
                        )}
                    </>
                )}
            </div>
        </div>
    );
};
