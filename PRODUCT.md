# Product decisions

A broken question costs students more than a lost mark. It can consume their study time, undermine a correct method, and leave them doubting what they understand. A repeated defect spreads that cost to every student who encounters it. Faculty also lose time when vague reports and duplicate complaints obscure the underlying issue.

The console groups reports by question and puts likely grading defects first, followed by distinct reporters and report age. It presents the evidence, exact change and decision together. Answer letters appear beside their option text. Original HTML and MathML remain available for checking, and each student gets a reply appropriate to their report. An approval records the exact before/after and succeeds only against the version faculty reviewed.

I would never let AI invent a missing diagram or distractor, rewrite an unsound question into a different problem, or change syllabus and chapter classification solely from a student's claim. Those decisions require subject expertise and authored content. Eligible repairs still need evidence and scope checks. An answer-key change needs an independent derivation that addresses the actual question, options and answer form. In this build, all 27 defect reports await faculty review because trusted automatic verification is still pending. The other 17 reports require no content change.

In week one I would track the **harmful-change rate among independently audited applied repairs**. Each sampled repair would receive a second faculty check against its original question and approved edit. The numerator is applied changes judged harmful and the denominator is all sampled applied changes. The sample size would accompany the rate. Acceptance clicks alone would give an incomplete picture of whether students benefit.

Beyond the brief, I would propose a **related-question investigation queue**. A confirmed defect could flag questions sharing its source, stem or solution template. Each flagged question would receive its own verification and review. That could uncover recurring import errors and protect students who have not yet reported them.

The deployed console uses explicitly precomputed analysis. Its public demo has a shared workspace. The selected free Render service can reset SQLite review history when it restarts or spins down.
