import { useEffect, useRef } from 'react';
import { qualityColor } from '../moveQuality';
import type { MoveRow } from '../types/postmortem';

/**
 * The game as played, one row per move pair, every half-move clickable.
 *
 * Laid out as White/Black columns under a move number rather than as a flat
 * list of plies, because that is how a scoresheet reads and how anyone who
 * plays chess already scans a game. The current half-move is marked in both
 * the list and on the board, and clicking any of them navigates straight
 * there - "do not make navigation cumbersome" is the spec's phrasing, and a
 * list you can only step through one ply at a time is exactly that.
 *
 * The grade dot is the only colour in the row. It reuses the shared palette
 * (../moveQuality), so a blunder here is the same red as a blunder in the
 * live game - and grades arrive over the minute the scan takes, so a row
 * without one yet simply has no dot rather than a placeholder.
 */
export function PostMortemMoveList({
    moves,
    currentId,
    onSelect,
    disabled,
}: {
    moves: MoveRow[];
    currentId: string;
    onSelect: (nodeId: string) => void;
    disabled: boolean;
}) {
    const listRef = useRef<HTMLDivElement>(null);

    // Keep the move being looked at in view. Stepping through a long game
    // otherwise walks the highlight off the bottom of a scrolled panel, and
    // the list stops being a way to see where you are.
    useEffect(() => {
        const active = listRef.current?.querySelector('[data-current="true"]');
        active?.scrollIntoView({ block: 'nearest' });
    }, [currentId]);

    if (moves.length === 0) {
        return null;
    }

    // Plies grouped into scoresheet rows. A game that starts from a set-up
    // position can open with Black to move, which is why a Black ply is
    // allowed to start a row of its own rather than being assumed to follow a
    // White one.
    const pairs: { number: number; white?: MoveRow; black?: MoveRow }[] = [];
    for (const move of moves) {
        const last = pairs[pairs.length - 1];
        if (move.color === 'white') {
            pairs.push({ number: move.move_number, white: move });
        } else if (last && last.number === move.move_number && !last.black) {
            last.black = move;
        } else {
            pairs.push({ number: move.move_number, black: move });
        }
    }

    const cell = (move: MoveRow | undefined) => {
        if (!move) {
            return <span className="pm-move pm-move-empty" aria-hidden="true" />;
        }
        const isCurrent = move.node_id === currentId;
        return (
            <button
                type="button"
                className={`pm-move ${isCurrent ? 'is-current' : ''}`}
                data-current={isCurrent ? 'true' : undefined}
                onClick={() => onSelect(move.node_id)}
                disabled={disabled}
                aria-current={isCurrent ? 'true' : undefined}
                title={move.quality
                    ? `${move.san} - ${move.quality.name}${
                        typeof move.quality.cpl === 'number' ? ` (${move.quality.cpl}cp)` : ''}`
                    : move.san}
            >
                <span className="pm-move-san">{move.san}</span>
                {move.quality && (
                    <span
                        className="pm-move-grade"
                        style={{ backgroundColor: qualityColor(move.quality.label) }}
                        aria-label={move.quality.name}
                    />
                )}
                {/* A what-if exists instead of this move. Marked on the move
                    that was NOT taken, because that is the decision the
                    alternative answers. */}
                {move.has_branch && <span className="pm-move-branch" aria-label="has an alternative line">*</span>}
            </button>
        );
    };

    return (
        <div className="pm-moves" ref={listRef}>
            {pairs.map(pair => (
                <div className="pm-move-row" key={`${pair.number}-${pair.white?.ply ?? pair.black?.ply}`}>
                    <span className="pm-move-num">{pair.number}.</span>
                    {cell(pair.white)}
                    {cell(pair.black)}
                </div>
            ))}
        </div>
    );
}
