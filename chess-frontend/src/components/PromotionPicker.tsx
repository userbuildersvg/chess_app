import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { getCustomPieces } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';
import type { PendingPromotion, PromotionPiece } from './promotion';
import './PromotionPicker.css';

/**
 * Which piece a promoting pawn becomes.
 *
 * Every board in this app used to auto-queen. That is right about 97% of the
 * time and wrong in exactly the positions a learner most needs to understand:
 * the knight fork that wins on the spot, the rook that promotes without
 * stalemating, the bishop or knight that avoids handing back a draw. A chess
 * teaching tool that cannot underpromote is teaching a rule that does not
 * exist.
 *
 * Why this is ours and not react-chessboard's
 * -------------------------------------------
 *
 * The library ships a promotion dialog (`showPromotionDialog`,
 * `onPromotionPieceSelect`). It was not used, for three reasons:
 *
 * 1. It is styled entirely with inline styles and carries no class hooks, so
 *    it cannot be brought into the Obsidian system - only overridden by
 *    attribute-selector guesswork against a `title` string.
 * 2. It only opens on a DRAG. Click-to-move is a first-class way to play here
 *    (CLAUDE.md §19: "drag and click are both live, and neither is a mode"),
 *    so a dialog that appears one way and not the other would recreate exactly
 *    the inconsistency trap 13 was written about.
 * 3. It has no keyboard path and no cancel.
 *
 * So `autoPromoteToQueen` stays ON on all three boards - that is what stops
 * the library opening its own dialog on a drag - and both interactions route
 * a promotion here instead of submitting a move.
 *
 * Layout
 * ------
 *
 * It is positioned inside the board frame, over the promotion square, and
 * flips its growth direction so it never runs off the board: a promotion on
 * rank 8 drops downward from the square, one on rank 1 grows upward. That is
 * computed from the square and the board's ORIENTATION rather than from the
 * colour, so it stays correct on a rotated board - the one case a colour-based
 * rule gets wrong, and Review and Learn can now both be rotated.
 *
 * Because it lives inside the board frame it cannot reach the coaching panel,
 * the move list or the transport at any viewport, which is the rule trap 11
 * exists to enforce.
 */

type Props = {
    pending: PendingPromotion | null;
    /** The board's edge length in px - the picker sizes itself from squares. */
    boardSize: number;
    /** Which way the board is currently facing. */
    orientation: 'white' | 'black';
    pieceTheme: PieceThemeName;
    onSelect: (piece: PromotionPiece) => void;
    onCancel: () => void;
};

// Queen first, and focused on open: it is the answer nine times in ten, so the
// fast path stays "click the square, press Enter". The other three are in
// descending value after it, which is the order every chess interface uses and
// therefore the order a player's hand already knows.
const OPTIONS: { piece: PromotionPiece; code: string; label: string }[] = [
    { piece: 'q', code: 'Q', label: 'Queen' },
    { piece: 'r', code: 'R', label: 'Rook' },
    { piece: 'b', code: 'B', label: 'Bishop' },
    { piece: 'n', code: 'N', label: 'Knight' },
];

export const PromotionPicker: React.FC<Props> = ({
    pending, boardSize, orientation, pieceTheme, onSelect, onCancel,
}) => {
    const rootRef = useRef<HTMLDivElement>(null);
    const pieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    // Focus lands on Queen when the picker opens, so a keyboard user is not
    // dropped onto a modal question with no way in.
    useEffect(() => {
        if (!pending) return;
        const first = rootRef.current?.querySelector<HTMLButtonElement>('button');
        first?.focus();
    }, [pending]);

    const onKeyDown = useCallback((event: React.KeyboardEvent) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            onCancel();
            return;
        }
        // Arrow keys walk the four options. They are laid out in one line, so
        // both axes move along it rather than pretending there is a grid.
        const isBack = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
        const isNext = event.key === 'ArrowRight' || event.key === 'ArrowDown';
        if (!isBack && !isNext) return;
        event.preventDefault();
        const buttons = Array.from(
            rootRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? [],
        );
        const here = buttons.findIndex(b => b === document.activeElement);
        const next = (here + (isNext ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next]?.focus();
    }, [onCancel]);

    if (!pending) {
        return null;
    }

    const squareSize = boardSize / 8;
    const file = pending.to.charCodeAt(0) - 'a'.charCodeAt(0);
    const rank = Number(pending.to[1]);
    // Where the promotion square is drawn, which depends on which way round
    // the board is - not on who is promoting.
    const col = orientation === 'white' ? file : 7 - file;
    const row = orientation === 'white' ? 8 - rank : rank - 1;
    // Grow away from the edge the square sits on, so all four options are
    // always on the board. Row 0 is the top row as drawn.
    const growsDown = row === 0;
    const top = growsDown ? row * squareSize : (row + 1) * squareSize - squareSize * 4;

    const colorPrefix = pending.color === 'white' ? 'w' : 'b';

    return (
        <div
            className="promotion-scrim"
            /* A click anywhere else cancels. The scrim covers the board frame
               only - it is a question about a square, not a page-level modal,
               and it must not trap someone away from the rest of the app. */
            onMouseDown={event => {
                if (event.target === event.currentTarget) onCancel();
            }}
        >
            <div
                ref={rootRef}
                className={`promotion-picker ${growsDown ? 'grows-down' : 'grows-up'}`}
                style={{
                    left: col * squareSize,
                    top,
                    width: squareSize,
                    height: squareSize * 4,
                }}
                role="dialog"
                aria-modal="true"
                aria-label="Choose what the pawn becomes"
                onKeyDown={onKeyDown}
            >
                {OPTIONS.map(option => {
                    const Piece = pieces[`${colorPrefix}${option.code}`];
                    return (
                        <button
                            key={option.piece}
                            type="button"
                            className="promotion-option"
                            style={{ height: squareSize }}
                            onClick={() => onSelect(option.piece)}
                            title={`Promote to ${option.label.toLowerCase()}`}
                            aria-label={option.label}
                        >
                            {Piece
                                ? <Piece squareWidth={squareSize} />
                                : <span aria-hidden="true">{option.code}</span>}
                        </button>
                    );
                })}
            </div>
        </div>
    );
};
