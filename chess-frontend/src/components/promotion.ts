/** Pieces a pawn may become when it reaches the last rank. */
export type PromotionPiece = 'q' | 'r' | 'b' | 'n';

/** The pending move, held while the promotion question is on screen. */
export type PendingPromotion = {
    from: string;
    to: string;
    color: 'white' | 'black';
};

/** Return whether a from/to pair is a pawn reaching its promotion rank. */
export function isPromotionMove(fen: string, from: string, to: string): boolean {
    const placement = (fen || '').split(' ')[0];
    if (!placement || from.length < 2 || to.length < 2) return false;
    const rank = to[1];
    if (rank !== '8' && rank !== '1') return false;

    const rows = placement.split('/');
    const fromFile = from.charCodeAt(0) - 'a'.charCodeAt(0);
    const fromRank = Number(from[1]);
    if (Number.isNaN(fromRank) || fromRank < 1 || fromRank > 8) return false;
    const row = rows[8 - fromRank];
    if (!row) return false;

    let file = 0;
    for (const ch of row) {
        if (ch >= '1' && ch <= '9') {
            file += Number(ch);
            continue;
        }
        if (file === fromFile) {
            return (ch === 'P' && rank === '8') || (ch === 'p' && rank === '1');
        }
        file += 1;
        if (file > fromFile) return false;
    }
    return false;
}

/** The colour of the piece on `from`, used to label the promotion choices. */
export function moverColor(fen: string, from: string): 'white' | 'black' {
    const placement = (fen || '').split(' ')[0];
    const rows = placement.split('/');
    const fromFile = from.charCodeAt(0) - 'a'.charCodeAt(0);
    const row = rows[8 - Number(from[1])];
    if (!row) return 'white';
    let file = 0;
    for (const ch of row) {
        if (ch >= '1' && ch <= '9') {
            file += Number(ch);
            continue;
        }
        if (file === fromFile) return ch === ch.toUpperCase() ? 'white' : 'black';
        file += 1;
    }
    return 'white';
}
