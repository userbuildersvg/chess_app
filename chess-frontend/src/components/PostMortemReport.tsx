import { Chess } from 'chess.js';
import { useMemo, useState } from 'react';
import { EmptyState } from './EmptyState';
import { lossText, qualityColor } from '../moveQuality';
import type { CurvePoint, GameSummary, MoveRow, ScanProgress } from '../types/postmortem';

/**
 * What the engine found across the whole game: the evaluation curve, both
 * players' accuracy, and the handful of moves worth opening the review on.
 *
 * Deliberately not a dashboard. The spec is explicit that the interactive
 * review is the product and that analytics must not become a wall of charts,
 * so this panel answers exactly three questions - how did the game swing, how
 * accurately did each side play, and where should I look first - and every
 * answer is a link back to a position. Nothing here is decorative, and
 * nothing here is a number the engine did not produce.
 *
 * While the scan is running the panel shows what it has so far rather than a
 * spinner: a game analysed to move 20 is genuinely analysed to move 20, and
 * hiding that behind a progress bar wastes the minute the scan takes.
 */
/** The engine's move in SAN, from the position BEFORE `row`. */
function bestSanFor(rows: MoveRow[], row: MoveRow): string | null {
    const uci = row.quality?.best_move;
    if (!uci) return null;
    const i = rows.findIndex(r => r.ply === row.ply);
    const fenBefore = i > 0 ? rows[i - 1].fen : null;
    try {
        const board = fenBefore ? new Chess(fenBefore) : new Chess();
        const mv = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci.slice(4, 5) || undefined });
        return mv?.san ?? uci;
    } catch {
        return uci;
    }
}

/**
 * The one decision to open the review on: the player's own worst judged
 * move when the game knows who they were, else the worst move of either
 * side. Everything on the card is the engine's - grade, centipawn loss,
 * preferred move, depth - so it never says more than the scan found.
 */
function KeyDecision({ summary, moves, playerColor, onSelect }: {
    summary: GameSummary;
    moves: MoveRow[];
    playerColor: 'white' | 'black' | null;
    onSelect: (nodeId: string) => void;
}) {
    const [details, setDetails] = useState(false);
    const own = playerColor ? summary.turning_points.filter(t => t.color === playerColor) : summary.turning_points;
    const pool = own.length ? own : summary.turning_points;
    const pick = [...pool].sort((a, b) => (b.cpl ?? 0) - (a.cpl ?? 0))[0];
    const row = pick ? moves.find(m => m.ply === pick.ply) : undefined;
    if (!pick || !row) {
        return (
            <section className="pm-key" data-testid="pm-key-empty">
                <h3 className="pm-section-title">Your biggest learning opportunity</h3>
                <p className="pm-key-empty">
                    No major learning opportunity found yet. You can still explore the game, step
                    through the moves, or ask the coach about a position.
                </p>
            </section>
        );
    }
    const theirs = playerColor !== null && pick.color !== playerColor;
    const bestSan = bestSanFor(moves, row);
    // A mate-scale loss is capped at thousands of centipawns; "95.6 pawns"
    // is a number nobody thinks in. Say what it was.
    const decisive = pick.cpl != null && pick.cpl >= 1000;
    const pawns = pick.cpl != null && !decisive ? (pick.cpl / 100).toFixed(1) : null;
    const gradeName = row.quality?.name ?? pick.label;
    return (
        <section className="pm-key" data-testid="pm-key">
            <h3 className="pm-section-title">Your biggest learning opportunity</h3>
            <div className="pm-key-move">
                <span className="pm-move-grade" style={{ backgroundColor: qualityColor(pick.label) }} aria-hidden="true" />
                <span className="pm-key-san">
                    Move {Math.ceil(pick.ply / 2)}{pick.color === 'white' ? '.' : '...'} {pick.san}
                </span>
                <span className="pm-key-who">
                    {theirs ? 'Your opponent\'s move' : playerColor ? `You were ${pick.color === 'white' ? 'White' : 'Black'}` : `${pick.color === 'white' ? 'White' : 'Black'} to move`}
                </span>
            </div>
            <p className="pm-key-fact">
                A judged decision, not a book or forced move: graded <strong>{gradeName}</strong>
                {decisive ? <> — it turned the position into a <strong>lost one</strong></> : pawns ? <> — it lost about <strong>{pawns} pawns</strong> of evaluation</> : null}
                {bestSan ? <>; the engine preferred <strong>{bestSan}</strong></> : null}.
            </p>
            <div className="pm-key-actions">
                <button type="button" className="action-btn corr-primary" onClick={() => onSelect(row.node_id)} data-testid="pm-key-cta">
                    {theirs ? 'Look at this decision' : 'Work through this decision'}
                </button>
                <button type="button" className="pm-link" aria-expanded={details} onClick={() => setDetails(v => !v)}>
                    {details ? 'Hide engine details' : 'Show engine details'}
                </button>
            </div>
            {details && (
                <dl className="pm-key-details" data-testid="pm-key-details">
                    <div><dt>Grade</dt><dd>{gradeName}{row.quality?.confidence === 'low' ? ' (close call)' : ''}</dd></div>
                    <div><dt>Centipawn loss</dt><dd>{pick.cpl ?? '—'}</dd></div>
                    <div><dt>Engine preferred</dt><dd>{bestSan ?? '—'}</dd></div>
                    <div><dt>Phase</dt><dd>{pick.phase}</dd></div>
                    <div><dt>Depth</dt><dd>{row.quality?.depth ?? summary.depth ?? '—'}</dd></div>
                </dl>
            )}
        </section>
    );
}

export function PostMortemReport({
    scan,
    summary,
    curve,
    moves,
    currentPly,
    onSelect,
    onRetry,
    playerColor = null,
}: {
    scan: ScanProgress;
    summary: GameSummary | null;
    curve: CurvePoint[];
    moves: MoveRow[];
    currentPly: number;
    onSelect: (nodeId: string) => void;
    onRetry: () => void;
    /** Which side the person played, when the game knows. Their own
     *  decisions are listed first; the opponent's are labelled. */
    playerColor?: 'white' | 'black' | null;
}) {
    const nodeByPly = useMemo(() => {
        const map = new Map<number, string>();
        for (const move of moves) {
            map.set(move.ply, move.node_id);
        }
        return map;
    }, [moves]);

    const running = scan.status === 'running';
    const nothingYet = scan.analysed === 0;

    if (scan.status === 'idle' && nothingYet) {
        return (
            <EmptyState title="Not analysed yet" action={
                <button type="button" className="action-btn pm-report-retry" onClick={onRetry}>
                    Analyse this game
                </button>
            }>
                Stockfish will walk the game position by position and grade every move.
            </EmptyState>
        );
    }

    if (running && nothingYet) {
        return (
            <EmptyState title="Analysing the game" tone="thinking">
                Move {scan.analysed} of {scan.total}. You can step through the game while this runs.
            </EmptyState>
        );
    }

    return (
        <div className="pm-report">
            <div
                className={`pm-scan ${running ? 'is-active' : 'is-idle'}`}
                role="status"
                aria-live="polite"
                aria-hidden={!running}
            >
                <div className="pm-scan-track">
                    <div
                        className="pm-scan-fill"
                        style={{ width: `${running && scan.total ? (scan.analysed / scan.total) * 100 : 0}%` }}
                    />
                </div>
                <span className="pm-scan-label">
                    {running ? `Finding key decisions… Analysing move ${scan.analysed} of ${scan.total}` : '\u00a0'}
                </span>
            </div>

            {scan.status === 'failed' && scan.error && (
                <div className="pm-scan-error" role="alert">
                    <p>{scan.error}</p>
                    <button type="button" className="action-btn pm-report-retry" onClick={onRetry}>
                        Try again
                    </button>
                </div>
            )}

            <EvalCurve curve={curve} currentPly={currentPly} onSelect={onSelect} nodeByPly={nodeByPly} />

            {summary && !running && (
                <KeyDecision summary={summary} moves={moves} playerColor={playerColor} onSelect={onSelect} />
            )}

            {summary && (
                <dl className="pm-facts" data-testid="pm-facts">
                    <div>
                        <dt>Coverage</dt>
                        <dd>{summary.coverage.analysed_moves}/{summary.coverage.total_moves} half-moves analysed
                            {summary.coverage.skipped_moves > 0 ? ` · ${summary.coverage.skipped_moves} not graded` : ''}</dd>
                    </div>
                    <div>
                        <dt>Meaningful decisions</dt>
                        <dd>
                            {(['white', 'black'] as const).map(c => {
                                const side = summary[c];
                                const excluded = Object.values(side.excluded_from_score).reduce((a, b) => a + b, 0);
                                return <span key={c} className="pm-facts-side">{c === 'white' ? 'White' : 'Black'}: {side.scored} judged{excluded ? `, ${excluded} book/forced excluded` : ''}</span>;
                            })}
                        </dd>
                    </div>
                    <div>
                        <dt>Engine caveat</dt>
                        <dd>{summary.depth ? `Stockfish depth ${summary.depth}; ` : ''}close calls may shift with deeper analysis.</dd>
                    </div>
                </dl>
            )}

            {summary && (
                <div className={`pm-coverage ${summary.coverage.scope === 'partial_game' ? 'is-partial' : ''}`}>
                    <strong>
                        {summary.coverage.scope === 'full_game'
                            ? 'Whole-game analysis'
                            : 'Partial game analysis'}
                    </strong>
                    <span>
                        {summary.coverage.analysed_moves} of {summary.coverage.total_moves} half-moves analysed.
                        {summary.coverage.skipped_moves > 0
                            ? ` ${summary.coverage.skipped_moves} could not be graded because the engine scan stopped early.`
                            : ' Every move in the imported game was checked.'}
                    </span>
                </div>
            )}

            {summary && (
                <div className="pm-score-explainer">
                    <h3 className="pm-section-title">Decision accuracy</h3>
                    <p>
                        The percentage averages engine-judged decisions. Book and forced moves are
                        analysed and labelled, but excluded because they are not meaningful choices.
                    </p>
                </div>
            )}

            {summary && (
                <div className="pm-accuracy">
                    {(['white', 'black'] as const).map(color => {
                        const side = summary[color];
                        const exclusions = Object.entries(side.excluded_from_score)
                            .filter(([, count]) => count > 0)
                            .map(([label, count]) => `${count} ${label}`)
                            .join(', ');
                        return (
                            <div className="pm-accuracy-side" key={color}>
                                <span className="pm-accuracy-name">{color === 'white' ? 'White' : 'Black'}</span>
                                <span className="pm-accuracy-figure">
                                    {side.accuracy === null ? '--' : `${side.accuracy}%`}
                                </span>
                                <span className="pm-accuracy-sub">
                                    {side.analysed} of {side.total} moves analysed
                                </span>
                                <span className="pm-accuracy-sub">
                                    {side.scored} decision{side.scored === 1 ? '' : 's'} included in this score
                                </span>
                                {exclusions && (
                                    <span className="pm-accuracy-excluded">Excluded from score: {exclusions}</span>
                                )}
                                {side.skipped > 0 && (
                                    <span className="pm-accuracy-excluded is-warn">
                                        Not graded: {side.skipped}
                                    </span>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {summary && summary.turning_points.length > 0 && (
                <div className="pm-turning">
                    <h3 className="pm-section-title">Worth a second look</h3>
                    <ul className="pm-turning-list">
                        {[...summary.turning_points]
                            .sort((a, b) => (playerColor ? Number(b.color === playerColor) - Number(a.color === playerColor) : 0) || a.ply - b.ply)
                            .map(point => {
                            const nodeId = nodeByPly.get(point.ply);
                            const theirs = playerColor !== null && point.color !== playerColor;
                            return (
                                <li key={point.ply}>
                                    <button
                                        type="button"
                                        className="pm-turning-item"
                                        onClick={() => nodeId && onSelect(nodeId)}
                                        disabled={!nodeId}
                                    >
                                        <span
                                            className="pm-move-grade"
                                            style={{ backgroundColor: qualityColor(point.label) }}
                                            aria-hidden="true"
                                        />
                                        <span className="pm-turning-move">
                                            {Math.ceil(point.ply / 2)}{point.color === 'white' ? '.' : '...'} {point.san}
                                        </span>
                                        <span className="pm-turning-loss">
                                            {lossText(point.cpl, point.label)}
                                            {theirs && <span className="pm-turning-theirs"> · opponent</span>}
                                        </span>
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                </div>
            )}

            {summary && (
                <p className="pm-report-note">
                    {/* Provenance, not small print. Every figure above came from
                        one Stockfish pass at this depth, and a shallow search is
                        not the same claim as a deep one. */}
                    Engine line searched to depth {summary.depth ?? scan.depth ?? '?'}; labels and
                    percentages describe what this search suggests at that depth.
                    {summary.grades_unavailable.includes('great')
                        && ' A whole-game pass cannot tell a "Great" move from a "Best" one, so that grade is not used here.'}
                </p>
            )}
        </div>
    );
}

/**
 * The game's evaluation as one line, White above the axis and Black below.
 *
 * Clamped to +/-800 centipawns - eight pawns - because past that one side is
 * winning so decisively that the exact number stops meaning anything, and
 * without the clamp a single blunder pins the line to the edge and it never
 * moves again. The same compression the eval bars use, so the two read the
 * same way.
 *
 * Drawn as an inline SVG with no library: it is one path, an axis and a
 * marker, and every charting dependency is larger than the chart.
 */
function EvalCurve({
    curve,
    currentPly,
    onSelect,
    nodeByPly,
}: {
    curve: CurvePoint[];
    currentPly: number;
    onSelect: (nodeId: string) => void;
    nodeByPly: Map<number, string>;
}) {
    const width = 100;
    const height = 34;
    const clamp = 800;

    const points = useMemo(() => {
        const analysed = curve.filter(p => p.score !== null || p.mate_in !== null);
        if (analysed.length < 2) {
            return null;
        }
        const span = Math.max(1, curve.length - 1);
        return analysed.map(point => {
            const cp = point.mate_in !== null
                ? (point.mate_in >= 0 ? clamp : -clamp)
                : Math.max(-clamp, Math.min(clamp, point.score ?? 0));
            return {
                ply: point.ply,
                x: (point.ply / span) * width,
                y: height / 2 - (cp / clamp) * (height / 2),
            };
        });
    }, [curve]);

    if (!points) {
        return null;
    }

    const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ');
    const area = `${path} L${points[points.length - 1].x.toFixed(2)},${height / 2} L${points[0].x.toFixed(2)},${height / 2} Z`;
    const marker = points.find(p => p.ply === currentPly) ?? null;

    return (
        <div className="pm-curve">
            <svg
                viewBox={`0 0 ${width} ${height}`}
                preserveAspectRatio="none"
                className="pm-curve-svg"
                role="img"
                aria-label="Evaluation through the game, White above the centre line"
            >
                <path d={area} className="pm-curve-area" />
                <path d={path} className="pm-curve-line" />
                <line x1="0" y1={height / 2} x2={width} y2={height / 2} className="pm-curve-axis" />
                {marker && (
                    <circle cx={marker.x} cy={marker.y} r="1.6" className="pm-curve-marker" />
                )}
            </svg>
            {/* Clicking the curve is a nice-to-have; the move list is the
                primary way to navigate, so this stays a plain row of hit
                targets rather than a bespoke pointer interaction. */}
            <div className="pm-curve-hits">
                {points.map(point => {
                    const nodeId = nodeByPly.get(point.ply);
                    return nodeId ? (
                        <button
                            key={point.ply}
                            type="button"
                            className="pm-curve-hit"
                            style={{ left: `${(point.x / width) * 100}%` }}
                            onClick={() => onSelect(nodeId)}
                            aria-label={`Go to half-move ${point.ply}`}
                        />
                    ) : null;
                })}
            </div>
        </div>
    );
}
