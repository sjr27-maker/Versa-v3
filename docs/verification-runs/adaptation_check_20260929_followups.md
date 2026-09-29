# Adaptation check -- learner sooraj (2026-09-29 20:57)

Real Gemini, real server (`versa serve`), signed in with the tester name sign-in. A simulated student with a hidden persona drove every chat. **Staged verification**: shows the mechanisms working end to end on real models, not that Versa learned a real person.

Persona: _You learn by doing. On any new topic, the FIRST thing you want is one concrete worked case -- actual numbers or a specific instance. Once you've seen that, you want to know where it is actually used in real life. You rarely care for analogies or 'imagine that' pictures, and harder or rigorous versions put you off. Every so often, instead of tapping a card, you ask your own short follow-up question about the SAME topic, in your own words. You write casually and briefly._

- guesses revealed: 12, right: 7
- first third right: 3/4; last third right: 2/4
- answers shaped, by kind of turn: clicked_option normal x2, new_topic normal x5, new_topic_midchat normal x5, own_follow_up normal x3, own_follow_up shaped x3, picked_card normal x12
- cards re-offering one already taken in that chat: 0
- cards taken (by kind): {'where it is used': 8, 'work through one concrete example': 4}

## Ledger after the run (`versa observations`)

```
learner 4cb52e08-3c01-4a86-b32f-17c22598fbaa -- 153 observations (obs-v1)
  style     deeper x1, example x27, next x2, passed x16, use x26, why x2
  interest  returned_to_topic x4, topic_switch x1
  mood      decision_ms x74
  counted for less: 0 stuck turn(s), 0 rushed session(s)
  guesses: 32 of 58 picks guessed right
  thinking style (3 pattern(s)):
    [confirmed] On a question of their own, goes first to “where it is used”.
        ok  evidence     25 picks in this situation  (needs >= 4)
        ok  clear        64% go there  (needs >= 35%)
        ok  sessions     13 sessions  (needs >= 3)
        ok  topics       8 different topics  (needs >= 3)
        ok  above_cohort 2.5x other learners (26%)  (needs >= 1.5x)
        ok  over_time    earlier 60%, later 53%  (needs both >= 35%)
        ok  predicts     16 of 21 later picks  (needs >= 3 and >= 39% right)
    [confirmed] After “where it is used”, goes to “work through one concrete example”.
        ok  evidence     14 picks in this situation  (needs >= 4)
        ok  clear        60% go there  (needs >= 35%)
        ok  sessions     8 sessions  (needs >= 3)
        ok  topics       6 different topics  (needs >= 3)
        ok  above_cohort 1.8x other learners (33%)  (needs >= 1.5x)
        ok  over_time    earlier 61%, later 38%  (needs both >= 35%)
        ok  predicts     8 of 11 later picks  (needs >= 3 and >= 50% right)
    [emerging] After “work through one concrete example”, goes to “work through one concrete example”.
        ok  evidence     15 picks in this situation  (needs >= 4)
        ok  clear        38% go there  (needs >= 35%)
        ok  sessions     3 sessions  (needs >= 3)
        ok  topics       3 different topics  (needs >= 3)
        no  above_cohort 1.4x other learners (27%)  (needs >= 1.5x)
        no  over_time    earlier 31%, later 34%  (needs both >= 35%)
        no  predicts     1 of 5 later picks  (needs >= 3 and >= 41% right)
  way in: where it is used -> work through one concrete example
    After an answer to your own question, you went to “where it is used” first 19 of 25 times -- more than any other way in.
    After “where it is used”, you went to “work through one concrete example” 11 of 14 times.
```

## Chat 0 -- how compound interest works (you just opened a savings account)
- new_topic -- _Hey! I just opened my first savings account and want to learn how compound interest works._
- picked_card · guess HIT (expected where it is used, took where it is used, 5/10) -- _How does compound interest affect credit cards and loans_
- own_follow_up -- _Can you show me a concrete example with actual numbers? Like, if I owe $1,000 on a card wi_
- picked_card · guess miss (expected work through one concrete example, took where it is used, 4/10) -- _Show how high-yield savings accounts use compounding in my favor_
- new_topic_midchat -- _Hey, we're starting supply and demand in econ today. Can you show me a concrete example wi_
- picked_card · guess HIT (expected where it is used, took where it is used, 4/10) -- _Show how ride-sharing apps use surge pricing during rush hour_

## Chat 1 -- photosynthesis (biology homework)
- new_topic -- _Hey, can you show me a concrete, step-by-step example of photosynthesis with the actual nu_
- picked_card · guess HIT (expected where it is used, took where it is used, 5/10) -- _How farmers use this math to estimate crop yields_
- own_follow_up · SHAPED where it is used -> work through one concrete example -- _Can we do an actual calculation for a farm? Like, if a farmer has a specific amount of sun_
- picked_card · guess miss (expected where it is used, took work through one concrete example, 4/10) -- _Calculate the CO2 needed for 500 pounds of corn dry mass_
- new_topic_midchat · options offered: ["Yes, that's right", 'No, something else'] -- _Hey! Can you show me a specific example of a vaccine with its actual ingredients and exact_
- clicked_option -- _Yes, that's right_

## Chat 2 -- recursion in programming (you're learning Python)
- new_topic -- _Hey! Can you show me a simple recursive function in Python with some actual numbers so I c_
- own_follow_up · SHAPED where it is used -> work through one concrete example -- _Okay, I see how the numbers trace down to factorial(1). But where do programmers actually _
- own_follow_up -- _That folder code got cut off. Can you finish the python code for that find_file function a_
- picked_card · guess miss (expected where it is used, took work through one concrete example, 5/10) -- _Trace the step-by-step execution for finding dog.png_
- new_topic_midchat -- _Hey! We are starting the Pythagorean theorem in math today. Can you show me a quick exampl_
- picked_card · guess HIT (expected where it is used, took where it is used, 5/10) -- _Show how this is used to measure screen sizes or map distances_

## Chat 3 -- supply and demand (economics class)
- new_topic -- _hey! can we do supply and demand? could you show me a quick worked example with real numbe_
- picked_card · guess HIT (expected where it is used, took where it is used, 6/10) -- _Show me how surge pricing on Uber uses supply and demand_
- own_follow_up · SHAPED where it is used -> work through one concrete example -- _so can you show me the actual math or numbers for how the app decides to double the price _
- picked_card · guess miss (expected where it is used, took work through one concrete example, 6/10) -- _Finish solving the equation 300 M^-1.0 = 100 M^0.5 step by step_
- new_topic_midchat -- _hey! i want to learn how binary numbers work since i'm curious about computers. can you sh_
- picked_card · guess HIT (expected where it is used, took where it is used, 6/10) -- _Show how binary is used to store text characters using ASCII_

## Chat 4 -- how vaccines train the immune system (you're curious)
- new_topic · options offered: ["Yes, that's right", 'No, something else'] -- _Hey, I'm really curious about how vaccines train our immune system. Can you walk me throug_
- clicked_option -- _Yes, that's right_
- own_follow_up -- _this is cool but where exactly are these mrna vaccines actually being used right now besid_
- picked_card · guess miss (expected where it is used, took work through one concrete example, 5/10) -- _Walk through how a patient gets a custom melanoma mRNA shot_
- new_topic_midchat -- _Hey! We are starting Newton's third law in physics today. Can you show me a quick example _
- picked_card · guess HIT (expected where it is used, took where it is used, 6/10) -- _Show how rocket propulsion relies on this law to work in space_
