%% For a score's preview (render_preview() in build.py): included with
%% -dinclude-settings when LilyPond runs with -dpreview, which draws the first
%% system of what it is given. This gives it only the score that
%% $ROLLS_PREVIEW_SCORE names, numbered as lilyport's --score numbers them
%% (scores with only a \midi don't count), and without the titles, which the
%% page says already.

#(define rolls-wanted-score (string->number (getenv "ROLLS_PREVIEW_SCORE")))
#(define rolls-seen-scores 0)

#(define (rolls-engraved-score? score)
   (let ((defs (ly:score-output-defs score)))
     (or (null? defs)
         (any (lambda (def) (not (eq? (ly:output-def-lookup def 'output-def-kind #f) 'midi)))
              defs))))

%% LilyPond keeps parts and scores newest first. A book's scores include its
%% top-level markups, which have no music.
#(define (rolls-book-scores book)
   (append (append-map rolls-book-scores (reverse (ly:book-book-parts book)))
           (filter (lambda (score) (and (ly:score? score) (rolls-engraved-score? score)))
                   (reverse (ly:book-scores book)))))

#(define rolls-print-book toplevel-book-handler)
#(define rolls-module (current-module))

%% lilyport's compat.ly defines staff-space as 1, for sources that use it where
%% only the page's would be defined: enough for lilyport, which engraves no
%% page, but here the staves would take it, and shrink under full-size notes.
%% Taken away while the score is engraved, for the true one to be found; put
%% back for the rest of the file to parse.
#(define (rolls-print-with-true-staff-space book)
   (let ((defined? (module-defined? rolls-module 'staff-space))
         (compat (module-ref rolls-module 'staff-space #f)))
     (if defined? (module-remove! rolls-module 'staff-space))
     (rolls-print-book book)
     (if defined? (module-define! rolls-module 'staff-space compat))))

#(define (toplevel-book-handler book)
   (for-each
    (lambda (score)
      (set! rolls-seen-scores (1+ rolls-seen-scores))
      (if (= rolls-seen-scores rolls-wanted-score)
          (let ((paper (ly:book-paper book)))
            (ly:output-def-set-variable! paper 'bookTitleMarkup #f)
            (ly:output-def-set-variable! paper 'scoreTitleMarkup #f)
            (rolls-print-with-true-staff-space
             (ly:make-book paper (ly:book-header book) score)))))
    (rolls-book-scores book)))
