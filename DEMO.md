# Three-minute SecondServe demonstration

Use a fresh seeded development database. Keep three independent browser profiles/incognito contexts logged in as `student01`, `kitchen1`, and `admin`. All use the local-only password `SecondServe!2026`. Data is synthetic. Do not reuse these credentials for a real deployment.

The seed leaves **Demo cook-along · Golden lentil dal** planned, with already elapsed timings specifically so the demo needs no waiting. Future meals have genuine upcoming cutoffs. Current lunch is independently available for immediate reservations. The cook-along can be performed once per seed date; rerunning seed preserves the completed service. For another run, create a new meal slot through the UI with elapsed regular-service times and an upcoming pickup deadline, or use a separate disposable database.

## 0:00–0:30 · The student's intention

Say: “SecondServe helps a campus kitchen plan, share suitable surplus, and account for what actually happened.”

In the student profile, choose **Meal plans**, open an upcoming meal and select **I plan to attend** → **Save my plan**. Point out the cutoff and the explicit distinction: this is an intention, not a guaranteed reservation.

## 0:30–1:00 · Explainable kitchen planning

In the kitchen profile, open that upcoming meal's **Open operations** to show the live attending/unsure/no-response counts and arithmetic. Then return to Kitchen and open **Demo cook-along · Golden lentil dal**.

Show the forecast panel. Say: “These are configurable demonstration assumptions, not machine learning.” Click **Start preparation** without an override. The forecast becomes a saved snapshot; intentions close for this meal.

## 1:00–1:30 · Prepare, serve, offer

Enter **Cumulative total prepared = 20**, **Cumulative total served normally = 0**, and save. Click **Start regular service**. Enter **Cumulative total served normally = 19** (prepared remains 20), and save.

Click **Open surplus collection**. Total offered defaults to one. Choose a pickup start a few minutes before the current campus time if necessary (after regular service ends), and a deadline at least 20 minutes in the future. Confirm suitable unserved food under campus procedures and save. Point out that kitchen totals are now frozen.

## 1:30–2:00 · Reserve and hand over

In the student profile, choose **Find surplus**, open the cook-along listing, note **1 portion available**, and reserve one. The private pickup code persists across refreshes. Return briefly to the listing to show **0 portions available**.

In the kitchen profile, choose **Verify pickup**, enter that code, and click **Check pickup code**. Confirm the student display name, meal and window, then click **Confirm handover · Mark collected**. A second handover is rejected.

## 2:00–2:30 · Close the loop

Return to the cook-along operations page. Show **Surplus collected = 1**. It was zero while the portion was only reserved.

Click **Reconcile & close service**. Enter discarded **0** and retained/transferred **0**, then submit. The equation reads **20 prepared = 19 regular + 1 collected + 0 discarded + 0 retained**. Closure records the actual destination of every portion.

## 2:30–3:00 · Honest campus reporting

In the administrator profile, open Reports. Show the synthetic-data label, collected-surplus primary metric, food outcomes, collection rate denominator and unbuffered forecast error. Date-filter the results and export CSV. Briefly show People, Settings or Audit.

Close with: “We record surplus collected. We do not assume every collected portion would have been wasted, and we do not invent carbon savings. Kitchen staff remain responsible for safe handling decisions. This is a working small-campus prototype.”
