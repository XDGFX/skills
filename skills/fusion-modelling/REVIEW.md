# Blind review

A second agent reads the finished model as the **stranger** would: with the model in front of
it and nothing else. It is not told what the part is or shown the script that built it. If a
feature cannot be understood without knowing its purpose, that is a finding.

## Steps

1. **Make the report.** Save the design, then run
   [`scripts/model_report.py`](scripts/model_report.py) through the Fusion MCP with `DOC` set to
   the document and `PICTURES` set to an empty directory, as its docstring shows. Write the
   report text to a file beside the pictures. Done when the directory holds the report and one
   picture per sketch.

2. **Brief the reviewer.** Start a fresh agent and give it exactly three things: the path to
   this skill's `SKILL.md`, the path to the report, and the path to the pictures. Ask it to:

    - read the rules, then the whole report, then every picture;
    - list each place the model breaks a rule, naming the rule number, the sketch or feature
      by its timeline name, and the change it would make;
    - list anything it could not understand, and any edit a person might plausibly want that
      it could not see how to make;
    - say which three findings matter most.

   Leave out the part's name, purpose and history, the script, and your own opinion of the
   model. Done when the reviewer has reported against every sketch in the timeline.

3. **Act on it.** Fix each finding you agree with, in the script or the model, and rebuild.
   For each one you leave, write one line saying why.

4. **Show the user.** Give them the findings you fixed in a sentence, and every finding you
   left with its reason, so a matter of taste lands with them. One pass: review again only if
   they ask.
