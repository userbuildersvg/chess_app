import React from 'react';
import type { BoardEnd } from '../boardState';
import './BoardEndState.css';

interface BoardEndStateProps {
    /** Null while the game is live - the overlay renders nothing. */
    end: BoardEnd | null;
    /** The mode's OWN reset action. This component never resets anything itself. */
    onReset: () => void;
    /** ...and the mode's own word for it, so nothing gets a second name. */
    resetLabel: string;
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
export const BoardEndState: React.FC<BoardEndStateProps> = ({ end, onReset, resetLabel }) => {
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
            </div>
        </div>
    );
};
