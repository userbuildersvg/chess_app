/**
 * The check / checkmate / stalemate / draw matrix, against the real module.
 *
 * `chess-frontend/src/boardState.ts` is the single reading of a position that
 * Play, Learn and Review all share - the checked king's square, whether the
 * game is over, and which of the three endings it is. Before it there were
 * three separate implementations, and the failure mode of three is that two
 * of them agree and the board, the alert strip and the end-state layer say
 * different things about the same position.
 *
 * This runs in node, in about a second, with no browser and no server. It is
 * the cheap half of the pair; tools/verify/interaction.mjs is the half that
 * drives the real thing.
 *
 *     node tools/verify/boardstate.mjs
 *
 * esbuild comes from chess-frontend's own node_modules (vite depends on it),
 * so there is nothing to install.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const FRONTEND = join(ROOT, 'chess-frontend');
const out = join(mkdtempSync(join(tmpdir(), 'boardstate-')), 'boardState.mjs');
execFileSync(join(FRONTEND, 'node_modules/.bin/esbuild'), [
    join(FRONTEND, 'src/boardState.ts'),
    '--bundle', '--format=esm', '--platform=node', `--outfile=${out}`,
], { stdio: ['ignore', 'ignore', 'inherit'] });
const { readBoardStatus } = await import(out);

let pass = 0, fail = 0;
const t = (name, fen, want, flags) => {
    const got = readBoardStatus(fen, flags);
    const errs = [];
    for (const [k, v] of Object.entries(want)) {
        const actual = k === 'endKind' ? (got.end?.kind ?? null)
            : k === 'headline' ? (got.end?.headline ?? null)
                : got[k];
        if (actual !== v) errs.push(`${k}: want ${JSON.stringify(v)}, got ${JSON.stringify(actual)}`);
    }
    if (errs.length) { fail++; console.log(`FAIL  ${name}\n      ${errs.join('\n      ')}`); }
    else { pass++; console.log(`ok    ${name}`); }
};

console.log('=== live positions ===');
t('the opening is not doing anything',
    'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    { inCheck: false, checkedKingSquare: null, isCheckmate: false, isStalemate: false, gameOver: false, endKind: null });

t('a king surrounded by its own pieces and not attacked is not an ending',
    'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1',
    { isStalemate: false, isCheckmate: false, gameOver: false });

console.log('\n=== check ===');
t('an ordinary check names the checked king\'s square',
    'rnbqkbnr/ppp2ppp/8/1B1pp3/4P3/8/PPPP1PPP/RNBQK1NR b KQkq - 1 3',
    { inCheck: true, checkedKingSquare: 'e8', isCheckmate: false, gameOver: false, endKind: null });

t('a check another piece can block is not mate',
    'R5k1/5ppp/8/8/8/8/8/1r4K1 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'g8', isCheckmate: false, gameOver: false, endKind: null });

t('a check the king can capture its way out of is not mate',
    '6k1/5pRp/8/8/8/8/8/6K1 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'g8', isCheckmate: false, gameOver: false, endKind: null });

t('a double check the king can run from is still just check',
    '4k3/8/8/8/8/8/4R3/2B1K3 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'e8', isCheckmate: false });

console.log('\n=== checkmate ===');
t('back-rank mate',
    'R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'g8', isCheckmate: true, isStalemate: false, gameOver: true,
        endKind: 'checkmate', headline: 'CHECKMATE' });

t("fool's mate, delivered by the queen",
    'rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3',
    { isCheckmate: true, endKind: 'checkmate' });

t('rook and king mate on the edge',
    'R6k/8/6K1/8/8/8/8/8 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'h8', isCheckmate: true, endKind: 'checkmate' });

t('smothered mate - the king is not on the edge and it is still mate',
    '6rk/5Npp/8/8/8/8/8/6K1 b - - 0 1',
    { isCheckmate: true, endKind: 'checkmate' });

t('a mated king is still reported as being in check',
    'R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1',
    { inCheck: true, checkedKingSquare: 'g8' });

{
    // The winner is the side NOT to move; getting this backwards is the
    // classic off-by-one in end-state copy.
    const s = readBoardStatus('R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1');
    const ok = s.end.detail.startsWith('White wins') && s.end.strip === 'Checkmate - White wins';
    if (ok) { pass++; console.log('ok    the winner named is the side that is NOT to move'); }
    else { fail++; console.log('FAIL  winner wording: ' + s.end.detail + ' / ' + s.end.strip); }
}

console.log('\n=== stalemate ===');
t('king-only stalemate',
    '7k/5Q2/6K1/8/8/8/8/8 b - - 0 1',
    { inCheck: false, checkedKingSquare: null, isCheckmate: false, isStalemate: true, gameOver: true,
        endKind: 'stalemate', headline: 'STALEMATE' });

t('stalemate with other pieces on the board, all of them stuck',
    'k7/p7/P7/8/8/8/8/1R5K b - - 0 1',
    { isStalemate: true, isCheckmate: false, endKind: 'stalemate' });

t('the king has no square but ANOTHER piece can move: not stalemate',
    '7k/p4Q2/6K1/8/8/8/8/8 b - - 0 1',
    { isStalemate: false, isCheckmate: false, gameOver: false, endKind: null });

t('no legal move AND in check is checkmate, never stalemate',
    'R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1',
    { isStalemate: false, isCheckmate: true });

console.log('\n=== the other draws ===');
t('insufficient material is a draw, not a stalemate',
    '7k/8/6K1/8/8/8/8/8 w - - 0 1',
    { gameOver: true, isCheckmate: false, isStalemate: false, endKind: 'draw', headline: 'DRAW' });

t('the fifty-move rule',
    '4k3/8/4r3/8/8/4R3/8/4K3 w - - 100 80',
    { gameOver: true, endKind: 'draw' });

console.log('\n=== the server has the last word ===');
t('server flags win: repetition is invisible in a FEN and only the server knows',
    '4k3/8/4r3/8/8/4R3/8/4K3 w - - 4 40',
    { gameOver: true, endKind: 'draw', isCheckmate: false, isStalemate: false },
    { is_check: false, is_checkmate: false, is_stalemate: false, is_game_over: true });

t('a false from the server is an answer, not a missing value',
    'R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1',
    { isCheckmate: false, gameOver: false, endKind: null },
    { is_check: true, is_checkmate: false, is_stalemate: false, is_game_over: false });

console.log('\n=== degenerate input ===');
t('no FEN yet', undefined, { gameOver: false, endKind: null, checkedKingSquare: null });
t('a FEN that is not one does not throw', 'not a fen', { gameOver: false, endKind: null });

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
