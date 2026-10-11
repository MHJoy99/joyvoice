/* JoyVoice site: reveal-on-scroll + copy buttons. Page works without it. */
(function(){
  var d=document,reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var els=[].slice.call(d.querySelectorAll('.rv'));
  if(reduce||!('IntersectionObserver' in window)){els.forEach(function(e){e.classList.add('in')});}
  else{
    var io=new IntersectionObserver(function(list){list.forEach(function(en){if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target)}})},{rootMargin:'0px 0px -8% 0px',threshold:.08});
    els.forEach(function(e,i){e.style.setProperty('--rd',((i%3)*0.08)+'s');io.observe(e)});
  }
  d.querySelectorAll('.cp').forEach(function(b){
    b.addEventListener('click',function(){
      var pre=b.parentNode.querySelector('pre');if(!pre)return;
      var t=pre.innerText.replace(/\u00a0/g,' ');
      var done=function(){var l=b.querySelector('span');var o=l.textContent;l.textContent='Copied';setTimeout(function(){l.textContent=o},1600)};
      if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fb)}else fb();
      function fb(){var a=d.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity='0';d.body.appendChild(a);a.select();try{d.execCommand('copy');done()}catch(e){}d.body.removeChild(a)}
    });
  });
})();
