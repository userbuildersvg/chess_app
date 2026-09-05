import { useMemo } from 'react';
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
export function PostMortemReport({
    scan,
    summary,
    curve,
    moves,
    currentPly,
    onSelect,
    onRetry,
}: {
    scan: ScanProgress;
    summary: GameSummary | null;
    curve: CurvePoint[];
    moves: MoveRow[];
    currentPly: number;
    onSelect: (nodeId: string) => void;
    onRetry: () => void;
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
            {running && (
                <div className="pm-scan" role="status" aria-live="polite">
                    <div className="pm-scan-track">
                        <div
                            className="pm-scan-fill"
                            style={{ width: `${scan.total ? (scan.analysed / scan.total) * 100 : 0}%` }}
                        />
                    </div>
                    <span className="pm-scan-label">
                        Analysing move {scan.analysed} of {scan.total}
                    </span>
                </div>
            )}

            {scan.status === 'failed' && scan.error && (
                <div className="pm-scan-error" role="alert">
                    <p>{scan.error}</p>
                    <button type="button" className="action-btn pm-report-retry" onClick={onRetry}>
                        Try again
                    </button>
                </div>
            )}

            <EvalCurve curve={curve} currentPly={currentPly} onSelect={onSelect} nodeByPly={nodeByPly} />

            {summary && (
                <div className="pm-accuracy">
                    {(['white', 'black'] as const).map(color => {
                        const side = summary[color];
                        return (
                            <div className="pm-accuracy-side" key={color}>
                                <span className="pm-accuracy-name">{color === 'white' ? 'White' : 'Black'}</span>
                                <span className="pm-accuracy-figure">
                                    {side.accuracy === null ? '--' : `${side.accuracy}%`}
                                </span>
                                <span className="pm-accuracy-sub">
                                    {side.graded} move{side.graded === 1 ? '' : 's'} graded
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}

            {summary && summary.turning_points.length > 0 && (
                <div className="pm-turning">
                    <h3 className="pm-section-title">Worth a second look</h3>
                    <ul className="pm-turning-list">
                        {summary.turning_points.map(point => {
                            const nodeId = nodeByPly.get(point.ply);
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
                    Searched to depth {summary.depth ?? scan.depth ?? '?'}.
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
