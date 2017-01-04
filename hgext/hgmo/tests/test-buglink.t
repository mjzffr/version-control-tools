  $ cat >> $HGRCPATH << EOF
  > [extensions]
  > buglink = $TESTDIR/hgext/hgmo/buglink.py
  > EOF

  $ hg init repo
  $ cd repo
  $ touch foo
  $ hg -q commit -A -l - << EOF
  > bug 1 - summary line
  > 
  > bug 123456
  > 
  > ab4665521e2f
  > 
  > Aug 2008
  > 
  > b=#12345
  > 
  > GECKO_191a2_20080815_RELBRANCH
  > 
  > 12345 is a bug
  > 
  > foo 123456 whitespace!
  > EOF

  $ hg log -T '{desc|buglink}\n'
  <a href="https://bugzilla.mozilla.org/show_bug.cgi?id=1">bug 1</a> - summary line
  
  <a href="https://bugzilla.mozilla.org/show_bug.cgi?id=123456">bug 123456</a>
  
  ab4665521e2f
  
  Aug 2008
  
  <a href="https://bugzilla.mozilla.org/show_bug.cgi?id=12345">b=#12345</a>
  
  GECKO_191a2_20080815_RELBRANCH
  
  <a href="https://bugzilla.mozilla.org/show_bug.cgi?id=12345">12345</a> is a bug
  
  foo <a href="https://bugzilla.mozilla.org/show_bug.cgi?id=123456">123456</a> whitespace!