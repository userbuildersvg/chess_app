import type React from 'react';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';

/**
 * One reading of "what is this position doing" for all three modes.
 *
 * Play, Learn and Review each had their own answer to check / checkmate /
 * stalemate before this: Play read four booleans off the server, Learn ran
 * chess.js in a `useMemo`, Review switched on a server-side status string.
 * Three implementations of one chess rule is three chances to disagree, and
 * the UI treatments hanging off them (a red king square, a frozen board, an
 * end-state overlay) have to agree or they contradict each other on screen.
 *
 * So: one function, and the authority stays where it already was.
 *
 * - The **booleans** come from the server when the caller has them
 *   (`flags`). python-chess generated them for this exact position and is
 *   what will validate the next move, so nothing here is allowed to
 *   overrule it. Learn and Review pass no flags and get chess.js's reading
 *   of the same FEN, which is the reading they already used.
 * - The **checked king's square** and the **reason a draw is a draw** are
 *   derived from the FEN with chess.js. Neither is on the wire, and both
 *   are a pure function of the position, so deriving them cannot introduce
 *   a second opinion about legality - there is no legality question here.
 *
 * The one thing chess.js genuinely cannot see from a bare FEN is threefold
 * repetition, which needs the move history. That is exactly why `flags`
 * wins: the server says the game is over, and we fall back to a plain
 * "Draw" rather than guessing at the reason.
 */

export type EndKind = 'checkmate' | 'stalemate' | 'draw';

export type BoardEnd = {
    kind: EndKind;
    /** The one word over the board. */
    headline: string;
    /** The line under it - who won, or why it is a draw. */
    detail: string;
    /** The one-line form, for a single-line alert strip beside other text. */
    strip: string;
};

export type BoardStatus = {
    inCheck: boolean;
    /** The square of the king that is in check, for the board's own red mark. */
    checkedKingSquare: Square | null;
    isCheckmate: boolean;
    isStalemate: boolean;
    gameOver: boolean;
    /** Null while the game is live; the end-state overlay's whole content otherwise. */
    end: BoardEnd | null;
};

/** Server-side truth, where the caller has it. */
export type StatusFlags = {
    is_check?: boolean;
    is_checkmate?: boolean;
    is_stalemate?: boolean;
    is_game_over?: boolean;
};

const EMPTY: BoardStatus = {
    inCheck: false,
    checkedKingSquare: null,
    isCheckmate: false,
    isStalemate: false,
    gameOver: false,
    end: null,
};

/** Where the side to move keeps its king, or null if the FEN has none. */
const kingSquareOf = (board: Chess): Square | null => {
    const side = board.turn();
    for (const row of board.board()) {
        for (const square of row) {
            if (square && square.type === 'k' && square.color === side) {
                return square.square;
            }
        }
    }
    return null;
};

/**
 * Names the draw, when the position alone is enough to name it.
 *
 * Insufficient material and the fifty-move rule are both readable from a
 * FEN (the halfmove clock is in it). Repetition is not, and gets the
 * unqualified word.
 */
const drawReason = (board: Chess): { detail: string; strip: string } => {
    if (board.isInsufficientMaterial()) {
        return {
            detail: 'Draw - neither side has enough material to mate',
            strip: 'Draw - insufficient material',
        };
    }
    if (board.isDrawByFiftyMoves()) {
        return { detail: 'Draw by the fifty-move rule', strip: 'Draw - fifty-move rule' };
    }
    if (board.isThreefoldRepetition()) {
        return { detail: 'Draw by repetition', strip: 'Draw - repetition' };
    }
    return { detail: 'The game is drawn', strip: 'Draw' };
};

/**
 * The position after `uci` is played on `fen`, or null if chess.js refuses.
 *
 * For the OPTIMISTIC render: react-chessboard expects the `position` prop to
 * change in the same tick as a drop (it flags the drop and skips the
 * animation for it), and a board that waits for the server instead shows
 * the dragged piece snapping home for the round trip and then teleporting.
 * So Learn and Review show this position at once and let the server's FEN
 * replace it - and revert if the server refuses. Play does the same through
 * chessService.applyLocalMove. Legality is still the server's call.
 */
export const applyUci = (fen: string, uci: string): string | null => {
    try {
        const game = new Chess(fen);
        game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4, 5) || undefined });
        return game.fen();
    } catch {
        return null;
    }
};

/**
 * The two squares of the move that produced this position, styled.
 *
 * One treatment for each end, because the question a player asks is "what
 * just moved, and from where" - a direction, not a pair. The origin is a
 * ring around an almost-clear square (the piece has left; the square only
 * has to be noticed) and the destination is a warm fill under the piece
 * that landed, which is the one that has to read at a glance. Both are
 * tokens, so light and dark are tuned separately and a theme switch needs
 * no re-render.
 *
 * react-chessboard puts these styles on the square's INNER div (CLAUDE.md
 * trap 12), which is why a box-shadow ring works here at all: it is drawn
 * inside the square, over the board colour and under the piece.
 *
 * Applied FIRST by every caller, so a selection, a legal-move hint or the
 * checked king - all of which are about what happens next - paint over it.
 */
export const lastMoveStyles = (uci: string | null | undefined): Record<string, React.CSSProperties> => {
    if (!uci || uci.length < 4) return {};
    return {
        [uci.slice(0, 2)]: {
            backgroundColor: 'var(--sq-last-from)',
            boxShadow: 'inset 0 0 0 3px var(--sq-last-ring)',
        },
        [uci.slice(2, 4)]: {
            backgroundColor: 'var(--sq-last)',
            boxShadow: 'inset 0 0 0 2px var(--sq-last-ring)',
        },
    };
};

export const readBoardStatus = (
    fen: string | null | undefined,
    flags?: StatusFlags,
): BoardStatus => {
    if (!fen) {
        return EMPTY;
    }
    let board: Chess;
    try {
        board = new Chess(fen);
    } catch {
        return EMPTY;
    }

    // `??` and not `||`: `false` from the server is an answer, not a miss.
    const isCheckmate = flags?.is_checkmate ?? board.isCheckmate();
    const isStalemate = flags?.is_stalemate ?? board.isStalemate();
    const inCheck = flags?.is_check ?? board.inCheck();
    const gameOver = flags?.is_game_over ?? board.isGameOver();

    // Checkmate is check plus no legal reply, and stalemate is the same
    // "no legal reply" WITHOUT the check - which is the only thing that
    // separates a loss from a draw. Both of those tests happen inside
    // python-chess or chess.js; nothing here re-derives them, because a
    // fourth hand-rolled king-can't-move heuristic is precisely the bug
    // this file exists to make impossible.
    const sideToMove = board.turn() === 'w' ? 'White' : 'Black';
    const winner = board.turn() === 'w' ? 'Black' : 'White';

    let end: BoardEnd | null = null;
    if (isCheckmate) {
        end = {
            kind: 'checkmate',
            headline: 'CHECKMATE',
            detail: `${winner} wins - ${sideToMove} is in check with no legal move`,
            strip: `Checkmate - ${winner} wins`,
        };
    } else if (isStalemate) {
        end = {
            kind: 'stalemate',
            headline: 'STALEMATE',
            detail: `Draw - ${sideToMove} has no legal move and is not in check`,
            strip: 'Stalemate - a draw',
        };
    } else if (gameOver) {
        end = { kind: 'draw', headline: 'DRAW', ...drawReason(board) };
    }

    return {
        inCheck,
        checkedKingSquare: inCheck ? kingSquareOf(board) : null,
        isCheckmate,
        isStalemate,
        gameOver,
        end,
    };
};
