/**
 * The judge path's bundled game (pages/Judge.tsx): Morphy's Opera Game,
 * Paris 1858 - public domain, 33 plies, and short enough that Review's scan
 * finishes in seconds. Bundled rather than fetched, so the demo depends on no
 * external import. It goes through the ordinary import and scan like any PGN;
 * nothing about its analysis is precomputed.
 */
export const SAMPLE_PGN = `[Event "Paris Opera"]
[Site "Paris"]
[Date "1858.11.02"]
[White "Morphy"]
[Black "Duke Karl / Count Isouard"]
[Result "1-0"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
`;

export const SAMPLE_NAME = 'Sample game - Morphy, Paris 1858';

/**
 * Set by the judge page, consumed once by PostMortem on mount: "open the
 * sample". sessionStorage, like App's REVIEW_FOCUS_KEY, so it lasts one tab.
 */
export const SAMPLE_KEY = 'zugzwang-open-sample';
