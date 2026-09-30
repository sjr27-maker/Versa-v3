# Adaptation check -- learner sooraj (2026-09-29 18:06)

Real Gemini, real server (`versa serve`), signed in with the tester name sign-in. A simulated student with a hidden persona drove every chat. **Staged verification**: shows the mechanisms working end to end on real models, not that Versa learned a real person.

Persona: _You learn by doing. On any new topic, the FIRST thing you want is one concrete worked case -- actual numbers or a specific instance. Once you've seen that, you want to know where it is actually used in real life. You rarely care for analogies or 'imagine that' pictures, and harder or rigorous versions put you off. Every so often, instead of tapping a card, you ask your own short follow-up question about the SAME topic, in your own words. You write casually and briefly._

- guesses revealed: 27, right: 18
- first third right: 5/9; last third right: 8/9
- answers shaped, by kind of turn: clicked_option normal x2, new_topic normal x6, picked_card normal x28
- cards re-offering one already taken in that chat: 0
- cards taken (by kind): {'where it is used': 9, 'work through one concrete example': 14, 'why it works': 2, 'what comes next': 1, 'go further': 1}

## Ledger after the run (`versa observations`)

```
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "C:\Users\Sooraj\Desktop\Versa-v3\.venv\Scripts\versa.exe\__main__.py", line 10, in <module>
  File "C:\Users\Sooraj\Desktop\Versa-v3\src\versa\cli.py", line 694, in main
    asyncio.run(_observations(args.learner))
  File "C:\Users\Sooraj\AppData\Local\Programs\Python\Python312\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "C:\Users\Sooraj\AppData\Local\Programs\Python\Python312\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Sooraj\AppData\Local\Programs\Python\Python312\Lib\asyncio\base_events.py", line 664, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "C:\Users\Sooraj\Desktop\Versa-v3\src\versa\cli.py", line 263, in _observations
    patterns = await StyleReader(pool).patterns(learner.id)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Sooraj\Desktop\Versa-v3\src\versa\style_patterns.py", line 341, in patterns
    topics = assign_topics(rows, RetrievalConfig().same_subject_threshold)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Sooraj\Desktop\Versa-v3\src\versa\style_patterns.py", line 133, in assign_topics
    vec = list(vec)
          ^^^^^^^^^
TypeError: 'HalfVector' object is not iterable
learner 4cb52e08-3c01-4a86-b32f-17c22598fbaa -- 72 observations (obs-v1)
  style     deeper x1, example x20, next x1, passed x1, use x11, why x2
  mood      decision_ms x36
  counted for less: 0 stuck turn(s), 0 rushed session(s)
  guesses: 21 of 35 picks guessed right
```

## Chat 0 -- how compound interest works (you just opened a savings account)
- new_topic -- _Hey! I just opened my first savings account and want to learn how compound interest actual_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 0/2) -- _How does compound interest affect credit card debt_
- picked_card · options offered: ['Would you like to see the breakdown with monthly payments included?', 'Are you assuming no monthly payments are made over those 6 months?'] -- _Calculate a $1,000 balance with 20% interest over 6 months_
- clicked_option -- _Would you like to see the breakdown with monthly payments included?_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 1/4) -- _Calculate how many months it takes to pay it off completely at $100_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 2/5) -- _Calculate how fast it pays off if I pay $150 a month_

## Chat 1 -- photosynthesis (biology homework)
- new_topic -- _Hey! I'm working on my biology homework about photosynthesis. Can you show me the actual c_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 3/7) -- _How plants use the glucose created in this reaction_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 4/8) -- _Walk through how a specific plant like a potato uses glucose_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 4/9) -- _How do humans extract and use potato starch in cooking and industry_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 5/10) -- _Walk through making a shiny stir-fry sauce with potato starch_
- picked_card · guess miss (expected work through one concrete example, took why it works, 5/10) -- _Explain why potato starch makes sauces glossier than cornstarch_

## Chat 2 -- recursion in programming (you're learning Python)
- new_topic -- _Hey! Can you show me a simple Python recursion example with actual numbers? I just want to_
- picked_card · guess HIT (expected where it is used, took where it is used, 7/10) -- _Show how recursion is used to search folder structures_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 7/10) -- _Walk through searching a three level deep mock folder tree_
- picked_card · guess miss (expected work through one concrete example, took what comes next, 6/10) -- _Pass the matching file path back up through the return statements_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 6/10) -- _Trace python code that stops a search loop once a path is returned_
- picked_card · guess miss (expected work through one concrete example, took why it works, 6/10) -- _Explain why remaining loop iterations in Call 2 get skipped_

## Chat 3 -- supply and demand (economics class)
- new_topic -- _Hey! Can we start with a concrete, real-life example of supply and demand with actual numb_
- picked_card · guess HIT (expected where it is used, took where it is used, 7/10) -- _Show how ride-sharing apps use dynamic pricing in real time_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 7/10) -- _Walk through a step-by-step calculation with driver payouts_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 7/10) -- _Show how delivery apps calculate driver pay during peak hours_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 7/10) -- _Calculate a delivery payout using a 1.5x boost multiplier_
- picked_card · guess miss (expected work through one concrete example, took go further, 6/10) -- _Calculate payout when a flat bonus stacks on top of a boost_

## Chat 4 -- how vaccines train the immune system (you're curious)
- new_topic · options offered: ['Would you like to focus on an mRNA vaccine, such as the Pfizer COVID-19 vaccine, to see how genetic instructions prompt cells to build proteins?', 'Would you prefer to focus on a traditional vaccine, such as the Flu or Hepatitis B shot, to see how introduced viral proteins trigger an immune response?'] -- _hey! i want to learn how vaccines actually train our immune system. can you show me a real_
- clicked_option -- _Would you like to focus on an mRNA vaccine, such as the Pfizer COVID-19 vaccine, to see ho_
- picked_card · guess HIT (expected where it is used, took where it is used, 6/10) -- _Show how this mRNA delivery tech is used for cancer therapies_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 6/10) -- _Walk through a step by step example of a personalized cancer vaccine_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 6/10) -- _Show clinical trials testing personalized vaccines for pancreatic can…_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 6/10) -- _Walk through a specific patient case from the BioNTech trial_

## Chat 5 -- the Pythagorean theorem (maths class)
- new_topic -- _hey! we're doing the pythagorean theorem in math. can you show me a real example with actu_
- picked_card · guess HIT (expected where it is used, took where it is used, 5/10) -- _How do construction workers or painters use this in real life_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 6/10) -- _Check a 6 by 8 corner using a 10 foot tape measure_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 6/10) -- _Calculate the diagonal for a 5-foot by 12-foot wall corner_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 7/10) -- _Calculate the diagonal for an 8-foot by 15-foot corner_
- picked_card · guess HIT (expected work through one concrete example, took work through one concrete example, 8/10) -- _Calculate the diagonal for a non-integer size like 4 by 5 feet_
