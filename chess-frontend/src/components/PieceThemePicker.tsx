import { PIECE_THEME_LIST, getCustomPieces } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';
import './PieceThemePicker.css';

/**
 * The piece-set picker, one component for Play, Learn and Settings.
 *
 * Each tile draws the set's own knight and king with the same components the
 * board uses, on a square of the board's own colours - the ground the pieces
 * will actually be seen against. This is Learn's picker, lifted out: Play's
 * showed a board-colour chip, which cannot tell the four sets apart because
 * getBoardColors returns the same pair for every one of them, and Settings
 * had a bare <select>. Showing the thing being chosen is the picker's job.
 */
export function PieceThemePicker({ value, onChange, ariaLabel = 'Piece set' }: {
    value: PieceThemeName;
    onChange: (theme: PieceThemeName) => void;
    ariaLabel?: string;
}) {
    return (
        <div className="piece-picker" role="group" aria-label={ariaLabel}>
            {PIECE_THEME_LIST.map(theme => {
                const pieces = getCustomPieces(theme.id);
                const WhiteKnight = pieces.wN;
                const BlackKing = pieces.bK;
                const active = value === theme.id;
                return (
                    <button
                        key={theme.id}
                        type="button"
                        className={`piece-picker-tile ${active ? 'is-active' : ''}`}
                        aria-pressed={active}
                        onClick={() => onChange(theme.id)}
                    >
                        <span className="piece-picker-pieces" aria-hidden="true">
                            {WhiteKnight && <WhiteKnight squareWidth={40} />}
                            {BlackKing && <BlackKing squareWidth={40} />}
                        </span>
                        <span className="piece-picker-label">{theme.label}</span>
                    </button>
                );
            })}
        </div>
    );
}
