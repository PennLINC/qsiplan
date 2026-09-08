(function(){
  var DATA = JSON.parse(document.getElementById('cohort-data').textContent);
  var METHODS = DATA.methods, HREF = DATA.hrefTemplate;
  var curMethod = DATA.defaultMethod || DATA.methods[0].key, curGran = DATA.defaultGranularity;
  var STRIPE = {
    good:{v:'--good',s:'--good-soft',l:'--good-line'},
    warn:{v:'--warn',s:'--warn-soft',l:'--warn-line'},
    info:{v:'--accent-2',s:'--accent-soft',l:'--accent-2'},
    crit:{v:'--crit',s:'--crit-soft',l:'--crit-line'}
  };
  function clVars(k){var s=STRIPE[k];return '--cl:var('+s.v+');--cl-soft:var('+s.s+');--cl-line:var('+s.l+')';}
  var BADGES='ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  function esc(s){return String(s).replace(/[&<>"']/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function href(key){return esc(HREF.replace('%s', encodeURIComponent(key)));}

  function partition(entities, mkey){
    var groups={};
    entities.forEach(function(e){
      var sig=e.byMethod[mkey].sig;
      (groups[sig]=groups[sig]||[]).push(e);
    });
    var classes=Object.keys(groups).map(function(sig){
      var members=groups[sig];
      var rep=members[0], f=rep.byMethod[mkey];
      return {sig:sig, members:members, rep:rep, f:f, count:members.length};
    });
    classes.sort(function(a,b){return b.count-a.count || a.sig.localeCompare(b.sig);});
    return classes;
  }

  function classify(cls, ref){
    var f=cls.f, rf=ref.f, rep=cls.rep, rrep=ref.rep;
    if(cls===ref) return {kind:f.errors>0?'crit':'good', title:'Reference workflow', diff:[
      {t:'good',g:'✓',h:'The cohort majority — every other class is diffed against this one.'}
    ]};
    var diff=[], kind='info', title='Variant workflow';
    if(rep.t2w && !rrep.t2w){ title='T2w present — anatomical SDC target'; kind='info';
      diff.push({t:'info',g:'≠',h:'Has a <code>T2w</code>; registration-based SDC gains a structural target the reference lacks.'}); }
    else if(!rep.t2w && rrep.t2w){ title='No T2w'; kind='info';
      diff.push({t:'info',g:'≠',h:'No <code>T2w</code> where the reference has one.'}); }
    if(rep.scans<rrep.scans){ title='Missing '+(rrep.scans-rep.scans)+' scan'+(rrep.scans-rep.scans>1?'s':''); kind='warn';
      diff.push({t:'warn',g:'−',h:'<b>'+(rrep.scans-rep.scans)+'</b> fewer DWI series than the reference ('+rep.scans+' vs '+rrep.scans+').'}); }
    else if(rep.scans>rrep.scans){ title='Extra '+(rep.scans-rrep.scans)+' scan'+(rep.scans-rrep.scans>1?'s':'');
      diff.push({t:'info',g:'+',h:'<b>'+(rep.scans-rrep.scans)+'</b> more DWI series than the reference.'}); }
    if(f.outputs!==rf.outputs) diff.push({t:'info',g:'◦',h:'<b>'+f.outputs+'</b> outputs vs <b>'+rf.outputs+'</b> in the reference.'});
    if(f.warnings>rf.warnings){ if(kind==='info')kind='warn';
      diff.push({t:'warn',g:'!',h:'<b>'+f.warnings+'</b> correction warning'+(f.warnings>1?'s':'')+' (reference: '+rf.warnings+').'}); }
    if(f.errors>0){ kind='crit';
      diff.push({t:'crit',g:'!',h:'<b>'+f.errors+'</b> blocking error'+(f.errors>1?'s':'')+' under this method.'}); }
    if(!diff.length) diff.push({t:'info',g:'≠',h:'Distinct plan signature <code>'+cls.sig+'</code> — same counts, different structure.'});
    return {kind:kind, title:title, diff:diff};
  }

  function renderClasses(){
    var g=DATA[curGran], classes=partition(g, curMethod), ref=classes[0];
    var host=document.getElementById('classes'); host.innerHTML='';
    var unit=curGran==='session'?'session':'subject';
    classes.forEach(function(cls,i){
      var info=classify(cls, ref);
      var kind=info.kind;
      var card=document.createElement('div');
      card.className='card'; card.style.cssText=clVars(kind);
      var badge=BADGES[i]||('#'+(i+1));
      var status={good:'clean',warn:'review',info:'note',crit:'blocked'}[kind];
      var members=cls.members.map(function(m){
        var flag=(m.rep?m.rep.t2w:m.t2w)?'':'';
        return '<a class="chip'+(kind==='warn'?' flag':'')+'" href="'+href(m.href)+'">'+esc(m.label)+'</a>';
      }).join('');
      card.innerHTML=
        '<button class="row" aria-expanded="false">'+
          '<span class="stripe"></span><span class="cbadge">'+badge+'</span>'+
          '<span class="cmain"><span class="ctitle">'+info.title+'</span>'+
            '<span class="cdesc">'+cls.f.outputs+' outputs · '+cls.f.runs+' runs · '+
              cls.count+' '+unit+(cls.count>1?'s':'')+' · <code>sig '+cls.sig+'</code></span></span>'+
          '<span class="cmeta">'+
            '<span class="metric"><span class="mv">'+cls.count+'</span><span class="mk">'+unit+'s</span></span>'+
            (cls.f.errors>0?'<span class="pill" style="'+clVars('crit')+'"><span class="dot"></span>'+cls.f.errors+' err</span>':'')+
            '<span class="pill"><span class="dot"></span>'+status+'</span>'+
          '</span>'+
          '<span class="chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span>'+
        '</button>'+
        '<div class="detail"><div class="detail-in"><div class="dpad">'+
          '<div><h4>Members · '+cls.count+' '+unit+(cls.count>1?'s':'')+'</h4>'+
            '<div class="members">'+members+'</div></div>'+
          '<div><h4>'+(cls===ref?'Notes':'How it differs from '+BADGES[0])+'</h4>'+
            '<ul class="dlist">'+info.diff.map(function(d){
              return '<li class="ditem '+d.t+'"><span class="g">'+d.g+'</span><span>'+d.h+'</span></li>';
            }).join('')+'</ul></div>'+
        '</div></div></div>';
      card.querySelector('.row').addEventListener('click',function(){
        var open=card.classList.toggle('open');
        this.setAttribute('aria-expanded',open?'true':'false');
      });
      host.appendChild(card);
    });
    // headline
    var totalErr=g.reduce(function(a,e){return a+e.byMethod[curMethod].errors;},0);
    var v=document.getElementById('verdict');
    var m=METHODS.filter(function(x){return x.key===curMethod;})[0];
    if(totalErr>0){
      v.style.setProperty('--vstripe','var(--crit)'); v.style.setProperty('--vcolor','var(--crit)');
      document.getElementById('vline').innerHTML='<span class="hl">'+m.label.split(' ')[0]+' is infeasible</span> for this cohort';
      document.getElementById('vsub').innerHTML=totalErr+' blocking error'+(totalErr>1?'s':'')+' under <code>'+m.cli+'</code>. Switch method to preview a runnable pipeline.';
    }else{
      v.style.setProperty('--vstripe','var(--good)'); v.style.setProperty('--vcolor','var(--good)');
      document.getElementById('vline').innerHTML='<span class="hl">'+classes.length+' workflow'+(classes.length>1?'s':'')+'</span>, 0 blocking errors';
      document.getElementById('vsub').innerHTML=g.length+' '+unit+'s under <code>'+m.cli+'</code>; the smallest classes are the ones to inspect.';
    }
    document.getElementById('errVal').textContent=totalErr;
    document.getElementById('errStat').classList.toggle('zero', totalErr===0);
    document.getElementById('wfVal').textContent=classes.length;
    document.getElementById('offVal').textContent=g.length-ref.count;
    document.getElementById('offKey').textContent='off-majority '+unit+'s';
  }

  function renderMatrix(){
    var sessions=DATA.session, subjects=DATA.subject;
    var cols=[]; sessions.forEach(function(e){ if(e.session && cols.indexOf(e.session)<0) cols.push(e.session);});
    cols.sort();
    var multi=cols.length>0;
    var sigClass={}; // session sig -> class index under current method
    partition(sessions, curMethod).forEach(function(c,i){ sigClass[c.sig]=i; });
    var byKey={}; sessions.forEach(function(e){ byKey[e.subject+'|'+(e.session||'')]=e;});
    var head='<tr><th></th>'+(multi?cols.map(function(c){return '<th>ses-'+c+'</th>';}).join(''):'<th>grouping</th>')+'<th>T2w</th></tr>';
    var kindOf=['good','warn','info','crit'];
    var body=subjects.map(function(s){
      var cells;
      if(multi){
        cells=cols.map(function(c){
          var e=byKey[s.subject+'|'+c];
          if(!e) return '<td><div class="mcell absent">—</div></td>';
          var k=kindOf[sigClass[e.byMethod[curMethod].sig]%4]||'info';
          return '<td><div class="mcell" style="'+clVars(k)+';background:var(--cl-soft);color:var(--cl);border-color:var(--cl-line)">'+e.scans+'</div></td>';
        }).join('');
      }else{
        var e=byKey[s.subject+'|'];
        var k=kindOf[sigClass[e.byMethod[curMethod].sig]%4]||'info';
        cells='<td><div class="mcell" style="'+clVars(k)+';background:var(--cl-soft);color:var(--cl);border-color:var(--cl-line)">'+e.scans+'</div></td>';
      }
      var t2=s.t2w?'<td><div class="mcell" style="'+clVars('info')+';background:var(--cl-soft);color:var(--cl);border-color:var(--cl-line)">T2w</div></td>'
                  :'<td><div class="mcell absent">—</div></td>';
      return '<tr><td class="rh"><a class="chip" href="'+href(s.href)+'">sub-'+esc(s.subject)+'</a></td>'+cells+t2+'</tr>';
    }).join('');
    document.getElementById('matrix').innerHTML='<thead>'+head+'</thead><tbody>'+body+'</tbody>';
  }

  function press(btn){ btn.parentNode.querySelectorAll('button').forEach(function(o){o.setAttribute('aria-pressed','false');}); btn.setAttribute('aria-pressed','true'); }
  document.querySelectorAll('button[data-method]').forEach(function(b){
    if(b.dataset.method===curMethod) b.setAttribute('aria-pressed','true');
    b.addEventListener('click',function(){press(b);curMethod=b.dataset.method;renderClasses();renderMatrix();});
  });
  document.querySelectorAll('button[data-gran]').forEach(function(b){
    if(b.dataset.gran===curGran) b.setAttribute('aria-pressed','true');
    b.addEventListener('click',function(){press(b);curGran=b.dataset.gran;renderClasses();});
  });
  renderClasses(); renderMatrix();
})();
