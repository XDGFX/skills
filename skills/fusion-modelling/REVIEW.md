# Blind review

A second agent reads the finished model as the **stranger** would: with the model in front of
it and nothing else. It is not told what the part is or shown the script that built it. If a
feature cannot be understood without knowing its purpose, that is a finding.

## Steps

1. **Make the report.** Save the design, then run
   [`scripts/model_report.py`](scripts/model_report.py) through the Fusion MCP with `DOC` set to
   the document, `PICTURES` set to an empty directory, and `SKETCHES` naming at most twelve
   sketches: those that shape the main solids and the mating features, and any the change
   touched. Write the report text to a file beside the pictures. Name the directory for nothing:
   `review/01`, not `review/actuator`. Done when the directory holds the report and a picture
   of each chosen sketch; the report covers every sketch either way. An assembly's report lists
   its occurrences and joints, and has pictures only for sketches of its own.

2. **Brief the reviewer.** Start a fresh agent and give it exactly three things: the path to
   this skill's `SKILL.md`, the path to the report, and the path to the pictures. Ask it to:

    - read the rules, then the whole report, then every picture;
    - list each place the model breaks a rule, naming the rule number, the sketch or feature
      by its timeline name, and the change it would make;
    - list anything it could not understand, and any edit a person might plausibly want that
      it could not see how to make;
    - say which three findings matter most.

   Tell it one thing about taste: a plain number on a sketch, used once, is what rule 12 asks
   for. A missing parameter is a finding only when the same value is typed in two places.

   Leave out the part's name, purpose and history, the script, and your own opinion of the
   model. The document's own name is in the report and cannot be helped. Done when the
   reviewer has reported against every sketch in the timeline, and every joint in an assembly.

3. **Act on it.** Fix each finding you agree with, in the script or the model, and rebuild.
   For each one you leave, write one line saying why.

4. **Show the user.** Give them the findings you fixed in a sentence, and every finding you
   left with its reason, so a matter of taste lands with them. One pass: review again only if
   they ask.
