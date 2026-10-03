"""Create: original Shorts for your own channel (docs/DECISIONS.md D108).

Where the rest of Clipper cuts campaign footage, Create makes a video from
nothing: a topic from the backlog, a script in the channel's own voice (with a
physics check), your voiceover (made by hand on ElevenLabs and dropped back in),
then stock footage and animated diagrams cut to each sentence, captions and a
cover. The finished video joins the library like any clip, under the channel's
own campaign, so posting, stats and "What's working" all apply.

  channel.py   who the channel is: persona, rules, example scripts
  store.py     topics and videos in the database
  topics.py    the backlog of ideas
  script.py    scripts, checked
  voice.py     the dropped-in voiceover, timed word by word
  stock.py     stock footage (Pixabay)
  diagrams.py  animated physics diagrams
  build.py     the finished Short
"""
