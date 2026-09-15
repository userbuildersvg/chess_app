import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import type { Square } from 'chess.js';
import { EmptyState } from './EmptyState';
import { learningService } from '../services/learningService';
import type {
    Correction,
    CorrectionEvidence,
    LearningReference,
    PracticeStart,
    RetestPosition,
} from '../types/learning';
import { getCustomPieces } from '../pieceThemes';
import type { PieceThemeName } from '../pieceThemes';
import './CorrectionPanel.css';

/**
 * The learning loop, as one continuous thing.
 *
 * Post-Mortem's other three tabs answer questions about the game. This one is
 * a sequence with an order that matters, and the order is the product:
 *
 *   what were you trying to do?  ->  here is what you missed  ->  now play
 *   the better move yourself  ->  here is a fresh position testing the same
 *   idea  ->  saved
 *
 * Intent comes FIRST, before the diagnosis exists. That is not a nicety: a
 * diagnosis written against what the player was actually attempting is a
 * different thing from one written against the centipawn drop, and asking
 * afterwards would only be collecting agreement with an answer already given.
 *
 * TWO BOARDS, ON PURPOSE
 * ----------------------
 * "Try the better move" happens on the REAL board next door, through
 * Post-Mortem's existing branch flow - the same legal-move handling, the same
 * drag and click, the same move tree. Nothing about that is reimplemented
 * here; this panel only asks for it and notices when it happened.
 *
 * The re-test is a different position from a different game, and it gets its
 * own small board inside this panel. That is deliberate. Painting someone
 * else's position onto the board that has been showing *your game* is exactly
 * the confusion Post-Mortem works hardest to avoid - the mode already spends
 * a coloured frame and a "What if:" label keeping a branch distinct from the
 * real game, and a practice position is further from the real game than a
 * branch is.
 */

type Phase = 'intent' | 'diagnosis' | 'practice';

interface CorrectionPanelProps {
    /** The open review, or null on the empty canvas. */
    gameId: string | null;
    /** The node the board is currently on. */
    nodeId: string | null;
    /** The move that produced it, for naming the decision. Null at the start. */
    moveLabel: string | null;
    /** The move on the board was the opponent's. The question is still
     *  asked - "what were you expecting" is a real decision - but the panel
     *  says whose move it is, so nobody is asked to explain a move they did
     *  not make without knowing it. */
    opponentMove?: boolean;
    /** False on the game's starting position - there is no decision to diagnose. */
    canDiagnose: boolean;
    /** Whether the board is on the game rather than in a what-if. */
    onMainline: boolean;
    pieceTheme: PieceThemeName;
    /** Turn on Post-Mortem's own explore mode, so the better move can be played. */
    onRequestExplore: () => void | Promise<void>;
    /** The last move the player branched with, so we can notice they tried it. */
    lastBranchUci: string | null;
}

const CONFIDENCE_WORDS: [number, string][] = [
    [0.75, 'Confident'],
    [0.5, 'Fairly confident'],
    [0.0, 'Tentative'],
];

function confidenceWord(value: number): string {
    return (CONFIDENCE_WORDS.find(([floor]) => value >= floor) ?? [0, 'Tentative'])[1];
}

function evalText(ev: { score: number | null; mate_in: number | null } | null): string {
    if (!ev) return 'unknown';
    if (ev.mate_in !== null) {
        if (ev.mate_in === 0) return 'checkmate';
        return `mate in ${Math.abs(ev.mate_in)} for ${ev.mate_in > 0 ? 'White' : 'Black'}`;
    }
    if (ev.score === null) return 'unknown';
    return `${ev.score >= 0 ? '+' : ''}${(ev.score / 100).toFixed(2)}`;
}

/** The evidence behind a card, rendered as facts with their provenance. */
function EvidenceList({ evidence }: { evidence: CorrectionEvidence[] }) {
    return (
        <ul className="corr-evidence-list">
            {evidence.map((item, index) => (
                <li key={`${item.node_id}-${index}`} className="corr-evidence-item">
                    <span className="corr-evidence-move">
                        {item.ply ? `${Math.ceil(item.ply / 2)}${item.color === 'white' ? '.' : '...'} ` : ''}
                        {item.san ?? 'a move'}
                    </span>
                    <span className="corr-evidence-detail">
                        {[
                            item.source_name,
                            item.best_san ? `engine preferred ${item.best_san}` : null,
                            `${evalText(item.eval_before)} → ${evalText(item.eval_after)}`,
                            item.cpl !== null && item.cpl > 0 ? `${item.cpl} centipawns` : null,
                            item.depth !== null ? `depth ${item.depth}` : null,
                        ].filter(Boolean).join(' · ')}
                    </span>
                    <span className="corr-evidence-intent">You said: “{item.intent}”</span>
                </li>
            ))}
        </ul>
    );
}

/** The re-test: one position, no numbers, no answer until it is answered. */
function RetestBoard({
    position,
    pieceTheme,
    disabled,
    onMove,
}: {
    position: RetestPosition;
    pieceTheme: PieceThemeName;
    disabled: boolean;
    onMove: (uci: string) => void;
}) {
    const [selected, setSelected] = useState<Square | null>(null);
    const customPieces = useMemo(() => getCustomPieces(pieceTheme), [pieceTheme]);

    // chess.js purely to know which squares a piece may go to, exactly as the
    // other three boards do. It is the same library the backend's legality
    // will agree with, and the backend re-checks the move anyway - this is for
    // the hints, not for the verdict.
    const board = useMemo(() => {
        try {
            return new Chess(position.fen);
        } catch {
            return null;
        }
    }, [position.fen]);

    const targets = useMemo(() => {
        const map = new Map<string, Set<string>>();
        if (!board) return map;
        for (const move of board.moves({ verbose: true })) {
            const set = map.get(move.from) ?? new Set<string>();
            set.add(move.to);
            map.set(move.from, set);
        }
        return map;
    }, [board]);

    useEffect(() => {
        setSelected(null);
    }, [position.fen]);

    const play = useCallback((from: string, to: string) => {
        // Auto-queen, matching every other board in this app - trap 13.
        const promotes = board?.get(from as Square)?.type === 'p'
            && (to[1] === '8' || to[1] === '1');
        onMove(`${from}${to}${promotes ? 'q' : ''}`);
    }, [board, onMove]);

    const squareStyles = useMemo(() => {
        const styles: Record<string, React.CSSProperties> = {};
        if (disabled || !selected) return styles;
        styles[selected] = { backgroundColor: 'var(--sq-selected)' };
        for (const target of targets.get(selected) ?? []) {
            styles[target] = {
                backgroundColor: board?.get(target as Square)
                    ? 'var(--sq-capture)'
                    : 'var(--sq-legal)',
            };
        }
        return styles;
    }, [selected, targets, board, disabled]);

    if (!board) {
        // A position that will not parse is a broken exercise, and the brief
        // is explicit: do not show a fake or invalid one.
        return (
            <p className="corr-note is-warn">
                This practice position could not be loaded, so it is not being shown.
            </p>
        );
    }

    return (
        <div className="corr-retest-board">
            <Chessboard
                position={position.fen}
                boardWidth={300}
                boardOrientation={board.turn() === 'w' ? 'white' : 'black'}
                arePiecesDraggable={!disabled}
                isDraggablePiece={({ sourceSquare }) => targets.has(sourceSquare)}
                onPieceDrop={(from, to) => {
                    setSelected(null);
                    if (disabled || !targets.get(from)?.has(to)) return false;
                    play(from, to);
                    return true;
                }}
                onSquareClick={(square: Square) => {
                    if (disabled) return;
                    if (selected && targets.get(selected)?.has(square)) {
                        setSelected(null);
                        play(selected, square);
                        return;
                    }
                    setSelected(targets.has(square) ? square : null);
                }}
                autoPromoteToQueen
                customSquareStyles={squareStyles}
                animationDuration={150}
                customPieces={customPieces}
                customDarkSquareStyle={{ backgroundColor: 'var(--board-dark)' }}
                customLightSquareStyle={{ backgroundColor: 'var(--board-light)' }}
            />
        </div>
    );
}

export function CorrectionPanel({
    gameId,
    nodeId,
    moveLabel,
    opponentMove = false,
    canDiagnose,
    onMainline,
    pieceTheme,
    onRequestExplore,
    lastBranchUci,
}: CorrectionPanelProps) {
    const [reference, setReference] = useState<LearningReference | null>(null);
    const [phase, setPhase] = useState<Phase>('intent');
    const [preset, setPreset] = useState<string | null>(null);
    const [freeText, setFreeText] = useState('');
    const [busy, setBusy] = useState(false);
    const [activity, setActivity] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [card, setCard] = useState<Correction | null>(null);
    const [source, setSource] = useState<'coach' | 'engine'>('coach');
    const [recurred, setRecurred] = useState(false);
    const [bestSan, setBestSan] = useState<string | null>(null);
    const [bestUci, setBestUci] = useState<string | null>(null);
    const [triedIt, setTriedIt] = useState(false);
    const [showEvidence, setShowEvidence] = useState(false);

    const [practice, setPractice] = useState<PracticeStart | null>(null);
    const [hint, setHint] = useState<string | null>(null);
    const [hintsUsed, setHintsUsed] = useState(0);
    const [result, setResult] = useState<
        { passed: boolean; bestSan: string; playedSan: string } | null
    >(null);
    const startedAt = useRef<number | null>(null);
    // The exercise is below a card that can run to a dozen lines, so it is
    // scrolled to rather than left for the player to find.
    const practiceRef = useRef<HTMLDivElement | null>(null);
    const flowRef = useRef<{
        active: boolean;
        completed: boolean;
        gameId: string | null;
        nodeId: string | null;
        correctionId: string | null;
    }>({ active: false, completed: false, gameId: null, nodeId: null, correctionId: null });
    // Starting an alternative deliberately navigates back one ply. Preserve
    // the diagnosis across that expected node change and the branch that
    // follows; it is the same correction cycle, not a newly selected move.
    const expectingAlternativePosition = useRef(false);
    const [diagnosedMoveLabel, setDiagnosedMoveLabel] = useState<string | null>(null);
    const selectedEventRef = useRef<string | null>(null);
    const recordedAlternativeRef = useRef<string | null>(null);

    const [others, setOthers] = useState<Correction[]>([]);

    useEffect(() => {
        let live = true;
        void learningService.reference()
            .then(r => { if (live) setReference(r); })
            .catch(() => { /* the panel still works; only the presets are lost */ });
        return () => { live = false; };
    }, []);

    const refreshOthers = useCallback(() => {
        void learningService.corrections()
            .then(r => setOthers(r.corrections))
            .catch(() => {});
    }, []);

    useEffect(refreshOthers, [refreshOthers]);

    useEffect(() => {
        const selection = gameId && nodeId ? `${gameId}:${nodeId}` : null;
        if (gameId && nodeId && canDiagnose && onMainline
                && !flowRef.current.active && selectedEventRef.current !== selection) {
            selectedEventRef.current = selection;
            learningService.event('key_decision_selected', {
                game_id: gameId,
                node_id: nodeId,
            });
        }
    }, [gameId, nodeId, canDiagnose, onMainline]);

    // Leaving the Correct tab or selecting another move is the observable
    // abandonment boundary. Merely viewing a decision is not abandonment;
    // the flow becomes active only after intent is submitted.
    useEffect(() => () => {
        const flow = flowRef.current;
        if (flow.active && !flow.completed) {
            learningService.event('correction_flow_abandoned', {
                game_id: flow.gameId ?? undefined,
                node_id: flow.nodeId ?? undefined,
                correction_id: flow.correctionId ?? undefined,
                operation: 'left_correction_panel',
                completed: false,
            });
            flow.active = false;
        }
    }, [gameId]);

    // Moving the board to a different decision abandons the one in progress.
    // Keeping a card on screen for a move you are no longer looking at is the
    // fastest way to make someone believe a diagnosis is about the wrong move.
    useEffect(() => {
        if (flowRef.current.active
                && (!onMainline || expectingAlternativePosition.current)) {
            expectingAlternativePosition.current = false;
            return;
        }
        const flow = flowRef.current;
        if (flow.active && !flow.completed) {
            learningService.event('correction_flow_abandoned', {
                game_id: flow.gameId ?? undefined,
                node_id: flow.nodeId ?? undefined,
                correction_id: flow.correctionId ?? undefined,
                operation: 'selected_another_decision',
                completed: false,
            });
            flow.active = false;
        }
        setPhase('intent');
        setCard(null);
        setDiagnosedMoveLabel(null);
        setPractice(null);
        setResult(null);
        setHint(null);
        setHintsUsed(0);
        setTriedIt(false);
        recordedAlternativeRef.current = null;
        setShowEvidence(false);
        setError(null);
        setActivity(null);
    }, [nodeId, onMainline]);

    // Noticing that the player actually played the engine's move on the real
    // board. The branch itself went through Post-Mortem; this only records it.
    useEffect(() => {
        if (!card || !lastBranchUci || !bestUci) return;
        if (lastBranchUci !== bestUci) return;
        const signature = `${card.id}:${lastBranchUci}`;
        if (recordedAlternativeRef.current === signature) return;
        recordedAlternativeRef.current = signature;
        setTriedIt(true);
        void learningService.branchTried(card.id, lastBranchUci).catch(() => {});
    }, [lastBranchUci, bestUci, card]);

    const submitIntent = useCallback(async () => {
        if (!gameId || !nodeId) return;
        const intent = freeText.trim()
            || reference?.intent_presets.find(p => p.id === preset)?.label
            || '';
        if (!intent) {
            setError('Say what you were going for - even “I wasn’t sure” is an answer.');
            return;
        }
        setBusy(true);
        setActivity('Preparing correction… Checking the engine line and asking the coach.');
        setError(null);
        const requestStarted = performance.now();
        flowRef.current = {
            active: true,
            completed: false,
            gameId,
            nodeId,
            correctionId: null,
        };
        try {
            const out = await learningService.diagnose(gameId, nodeId, intent, preset);
            setCard(out.correction);
            setDiagnosedMoveLabel(moveLabel);
            setSource(out.diagnosis_source);
            setRecurred(out.recurred);
            setBestSan(out.best_san);
            setBestUci(out.best_move);
            setPhase('diagnosis');
            flowRef.current.correctionId = out.correction.id;
            learningService.event('correction_viewed', {
                game_id: gameId,
                node_id: nodeId,
                correction_id: out.correction.id,
                theme: out.correction.theme,
                duration_ms: Math.round(performance.now() - requestStarted),
                completed: true,
            });
            learningService.event('explanation_rendered', {
                game_id: gameId,
                node_id: nodeId,
                correction_id: out.correction.id,
                operation: 'correction_card',
                duration_ms: Math.round(performance.now() - requestStarted),
                completed: true,
            });
            refreshOthers();
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'The coach could not answer just now.');
            learningService.event('correction_flow_error', {
                game_id: gameId,
                node_id: nodeId,
                operation: 'correction_generation',
                duration_ms: Math.round(performance.now() - requestStarted),
                error_category: 'request_failed',
                completed: false,
            });
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [gameId, nodeId, moveLabel, freeText, preset, reference, refreshOthers]);

    const beginAlternative = useCallback(async () => {
        expectingAlternativePosition.current = true;
        setBusy(true);
        setActivity('Returning to the position before this decision…');
        setError(null);
        try {
            await onRequestExplore();
        } catch (exc) {
            expectingAlternativePosition.current = false;
            setError(exc instanceof Error ? exc.message : 'That position could not be opened.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [onRequestExplore]);

    const respond = useCallback(async (status: 'accepted' | 'rejected') => {
        if (!card) return;
        try {
            const out = await learningService.setStatus(card.id, status);
            setCard(out.correction);
        } catch {
            setError('That did not save.');
        }
    }, [card]);

    const beginPractice = useCallback(async () => {
        if (!card) return;
        setBusy(true);
        setActivity('Preparing fresh practice… Finding a verified position with the same idea.');
        setError(null);
        setResult(null);
        setHint(null);
        setHintsUsed(0);
        try {
            const out = await learningService.startPractice(card.id);
            setPractice(out);
            setPhase('practice');
            startedAt.current = Date.now();
            window.requestAnimationFrame(() => {
                practiceRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
            });
        } catch (exc) {
            setError(exc instanceof Error ? exc.message : 'That position could not be loaded.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card]);

    const answer = useCallback(async (uci: string) => {
        if (!card) return;
        setBusy(true);
        setActivity('Checking your practice move…');
        setError(null);
        try {
            const elapsed = startedAt.current ? Date.now() - startedAt.current : null;
            const out = await learningService.attempt(card.id, uci, hintsUsed, elapsed);
            setResult({ passed: out.passed, bestSan: out.best_san, playedSan: out.played_san });
            setCard(out.correction);
            if (out.passed) {
                flowRef.current.completed = true;
            }
            refreshOthers();
        } catch (exc) {
            // A refused move must not look like a failed attempt - the player
            // has not answered yet, so nothing is recorded and nothing is said
            // about their understanding.
            setError(exc instanceof Error ? exc.message : 'That move was not accepted.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card, hintsUsed, refreshOthers]);

    const askHint = useCallback(async () => {
        if (!card) return;
        setBusy(true);
        setActivity('Preparing a hint…');
        try {
            const out = await learningService.hint(card.id);
            setHint(out.hint);
            setHintsUsed(n => n + 1);
        } catch {
            setError('No hint is available for this position.');
        } finally {
            setBusy(false);
            setActivity(null);
        }
    }, [card]);

    // --- what to render -----------------------------------------------------

    if (!gameId) {
        return (
            <EmptyState title="Bring a game first">
                Import a game in this mode, then open one of your decisions here to work on it.
            </EmptyState>
        );
    }

    if (!onMainline && !card) {
        return (
            <EmptyState title="You are in a what-if">
                Corrections are about the moves you actually played. Go back to the game and pick a
                decision from it.
            </EmptyState>
        );
    }

    if (!canDiagnose) {
        return (
            <EmptyState title="Pick a decision">
                Step to a move you played - or open one from <strong>Worth a second look</strong> in
                the Report tab - and work through it here.
            </EmptyState>
        );
    }

    return (
        <div className="corr-panel">
            <div className="corr-head">
                <span className="corr-eyebrow">Working on</span>
                <span className="corr-move">{diagnosedMoveLabel ?? moveLabel ?? 'this move'}</span>
            </div>

            <div
                className={`corr-progress ${activity ? 'is-active' : ''}`}
                role="status"
                aria-live="polite"
                aria-atomic="true"
            >
                {activity ?? '\u00a0'}
            </div>

            {error && <p className="corr-note is-warn" role="alert">{error}</p>}

            {phase === 'intent' && (
                <div className="corr-step">
                    {opponentMove && (
                        <p className="corr-note" data-testid="corr-opponent-note">
                            This was your opponent's move. To work on one of your own decisions,
                            step to a move you played - or answer for what you expected here.
                        </p>
                    )}
                    <h3 className="corr-question">
                        {opponentMove ? 'What were you expecting here?' : 'What were you trying to accomplish here?'}
                    </h3>
                    <p className="corr-sub">
                        Answer before the coach does. It diagnoses the decision you were actually
                        making, not the one the engine would have made.
                    </p>
                    <div className="corr-presets">
                        {(reference?.intent_presets ?? []).map(option => (
                            <button
                                key={option.id}
                                type="button"
                                className={`corr-chip ${preset === option.id ? 'is-on' : ''}`}
                                aria-pressed={preset === option.id}
                                onClick={() => setPreset(preset === option.id ? null : option.id)}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                    <label className="corr-field">
                        <span className="corr-field-label">In your own words (optional)</span>
                        <textarea
                            className="corr-textarea"
                            value={freeText}
                            maxLength={300}
                            rows={3}
                            placeholder="e.g. I thought the knight was doing nothing on g8"
                            onChange={e => setFreeText(e.target.value)}
                        />
                    </label>
                    <button
                        type="button"
                        className="action-btn corr-primary"
                        onClick={() => void submitIntent()}
                        disabled={busy || (!preset && !freeText.trim())}
                    >
                        {busy ? 'Preparing correction…' : 'Show me what I missed'}
                    </button>
                </div>
            )}

            {phase === 'diagnosis' && card && (
                <div className="corr-step">
                    <div className="corr-card">
                        <div className="corr-card-head">
                            <span className="corr-theme">{card.theme_label}</span>
                            {recurred && card.occurrence_count > 1 && (
                                <span className="corr-recur" title="The same pattern, filed under the same theme">
                                    Seen before · {card.occurrence_count}×
                                </span>
                            )}
                        </div>

                        {source === 'engine' && (
                            <p className="corr-note is-warn">
                                The coach could not be reached, so this is the engine's reading only.
                            </p>
                        )}

                        <p className="corr-missed"><strong>What you missed:</strong> {card.missed_factor}</p>
                        <p className="corr-diagnosis">{card.diagnosis}</p>
                        <p className="corr-rule"><strong>Next time:</strong> {card.correction_rule}</p>

                        <p className="corr-engine-caveat">
                            <strong>Engine caveat:</strong>{' '}
                            {card.evidence?.at(-1)?.depth
                                ? `The move label and line reflect Stockfish at depth ${card.evidence.at(-1)?.depth}. `
                                : 'The move label reflects the available engine search. '}
                            Close calls can change with a deeper search.
                        </p>

                        {card.uncertainty && (
                            <p className="corr-uncertainty"><strong>Caveat:</strong> {card.uncertainty}</p>
                        )}

                        <div className="corr-meta">
                            <span>{confidenceWord(card.confidence)}</span>
                            <button
                                type="button"
                                className="corr-link"
                                aria-expanded={showEvidence}
                                onClick={() => setShowEvidence(v => !v)}
                            >
                                {showEvidence ? 'Hide the evidence' : 'Why do you think this?'}
                            </button>
                        </div>

                        {showEvidence && card.evidence && <EvidenceList evidence={card.evidence} />}

                        <p className="corr-fineprint" data-testid="correction-storage-copy">
                            {card.saved_to_account
                                ? <>Saved to your account with this game. It counts toward your <a className="corr-link" href="/profile">improvement profile</a>.</>
                                : <>Kept only for this browser session. <a className="corr-link" href="/signup">Create an account</a> to save corrections to an improvement profile.</>}
                        </p>

                        <div className="corr-respond">
                            <button
                                type="button"
                                className={`corr-chip ${card.status === 'accepted' ? 'is-on' : ''}`}
                                onClick={() => void respond('accepted')}
                            >
                                That's fair
                            </button>
                            <button
                                type="button"
                                className={`corr-chip ${card.status === 'rejected' ? 'is-on' : ''}`}
                                onClick={() => void respond('rejected')}
                            >
                                That's not what I was doing
                            </button>
                        </div>
                    </div>

                    {phase === 'diagnosis' && (
                        <div className="corr-try">
                            <h3 className="corr-question">Now play it yourself</h3>
                            <p className="corr-sub">
                                {bestSan
                                    ? <>Play <strong>{bestSan}</strong> on the board to see what it does. Seeing the move and making it are not the same thing.</>
                                    : <>Play the move you think was better on the board and see what happens.</>}
                            </p>
                            {triedIt
                                ? <p className="corr-note is-good">You played it. That line is yours to explore.</p>
                                : (
                                    <button
                                        type="button"
                                        className="action-btn"
                                        onClick={() => void beginAlternative()}
                                        disabled={busy}
                                    >
                                        Let me play on the board
                                    </button>
                                )}
                            <button
                                type="button"
                                className="action-btn corr-primary"
                                onClick={() => void beginPractice()}
                                disabled={busy}
                            >
                                {busy ? 'Preparing practice…' : 'Test me on a fresh position'}
                            </button>
                        </div>
                    )}
                </div>
            )}

            {phase === 'practice' && card && (
                <button
                    type="button"
                    className="corr-collapsed"
                    onClick={() => setPhase('diagnosis')}
                >
                    <span className="corr-collapsed-theme">{card.theme_label}</span>
                    <span className="corr-collapsed-back">Back to the correction</span>
                </button>
            )}

            {phase === 'practice' && practice && (
                <div className="corr-step" ref={practiceRef}>
                    {!practice.available ? (
                        <>
                            <h3 className="corr-question">Practice unavailable for this correction</h3>
                            {/* Honest, and the reason is the interesting part:
                                a position is only used here when the engine
                                confirms one right answer. */}
                            <p className="corr-sub">{practice.reason}</p>
                            <button type="button" className="action-btn" onClick={() => setPhase('diagnosis')}>
                                Back to the correction
                            </button>
                        </>
                    ) : (
                        <>
                            <h3 className="corr-question">
                                {practice.position.from_your_game ? 'A real position from your game' : 'A different position, same idea'}
                            </h3>
                            <p className="corr-sub corr-notyours">
                                {practice.position.from_your_game
                                    ? 'This position is stored evidence from your analyzed game.'
                                    : 'This is not from your game. It is here to test whether the idea transfers.'}
                            </p>
                            <p className="corr-prompt">{practice.position.prompt}</p>
                            <RetestBoard
                                position={practice.position}
                                pieceTheme={pieceTheme}
                                disabled={busy || result !== null}
                                onMove={uci => void answer(uci)}
                            />
                            {!result && (
                                <div className="corr-practice-actions">
                                    <button type="button" className="corr-link" onClick={() => void askHint()} disabled={busy}>
                                        Give me a hint
                                    </button>
                                </div>
                            )}
                            {hint && !result && <p className="corr-note">{hint}</p>}
                            {result && (
                                <>
                                    <p className={`corr-note ${result.passed ? 'is-good' : 'is-warn'}`} role="status">
                                        {result.passed
                                            ? `Correction complete — ${result.bestSan}. You recognized the same idea in a fresh position.`
                                            : `You played ${result.playedSan}. The move was ${result.bestSan}.`}
                                    </p>
                                    {result.passed && card?.correction_rule && (
                                        <p className="corr-rule"><strong>Take this with you:</strong> {card.correction_rule}</p>
                                    )}
                                    <p className="corr-sub">
                                        Practice on this correction: {card?.practice_summary.passed ?? 0} of{' '}
                                        {card?.practice_summary.attempted ?? 0}.
                                    </p>
                                    <div className="corr-practice-actions">
                                        <button type="button" className="action-btn" onClick={() => void beginPractice()}>
                                            Another one
                                        </button>
                                        <button type="button" className="action-btn" onClick={() => setPhase('diagnosis')}>
                                            Back to the correction
                                        </button>
                                    </div>
                                </>
                            )}
                        </>
                    )}
                </div>
            )}

            {others.length > 0 && (
                <div className="corr-others">
                    <h3 className="corr-section-title">Your corrections</h3>
                    <ul className="corr-others-list">
                        {others.map(item => (
                            <li key={item.id} className="corr-others-item">
                                <span className="corr-others-theme">{item.theme_label}</span>
                                <span className="corr-others-meta">
                                    {item.occurrence_count > 1 ? `${item.occurrence_count}× · ` : ''}
                                    {item.practice_summary.attempted > 0
                                        ? `practice ${item.practice_summary.passed}/${item.practice_summary.attempted}`
                                        : 'not practised'}
                                </span>
                            </li>
                        ))}
                    </ul>
                    <p className="corr-fineprint">
                        {others.some(item => item.saved_to_account)
                            ? 'Saved to your account history.'
                            : 'This correction history lasts only for this browser/server session.'}
                    </p>
                </div>
            )}
        </div>
    );
}
