(() => {
  const root = document.getElementById('sap-desk-design');
  const home = document.getElementById('sd-home-screen');
  const journey = document.getElementById('journey');
  const stage = root.querySelector('.sd-journey-stage');
  const paper = root.querySelector('.sd-paper');
  const far = root.querySelector('.sd-paper-back-far');
  const near = root.querySelector('.sd-paper-back-near');
  const tab = root.querySelector('.sd-coral-tab');
  const arch = root.querySelector('.sd-stage-arch');
  const copies = [...root.querySelectorAll('[data-stage-copy]')];
  const panels = [...root.querySelectorAll('[data-paper-panel]')];
  const steps = [...root.querySelectorAll('[data-journey-step]')];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const small = matchMedia('(max-width:680px)');
  const clamp = (n,min=0,max=1) => Math.max(min,Math.min(max,n));
  let frame = 0, active = -1, start = 0, travel = 1;
  function measure(){
    if(home.hidden)return;
    start=window.scrollY+journey.getBoundingClientRect().top-parseFloat(getComputedStyle(stage).top);
    travel=Math.max(1,journey.offsetHeight-stage.offsetHeight);
  }
  function paint(){
    frame=0;if(home.hidden||document.hidden)return;
    const p=clamp((window.scrollY-start)/travel);
    const index=p<.34?0:p<.68?1:2;
    root.dataset.scrollProgress=p.toFixed(3);
    root.dataset.motionStage=String(index+1);
    if(!reduce.matches){
      const amplitude=small.matches ? .52 : 1;
      paper.style.transform=`translate3d(${(-10+20*p)*amplitude}px,${(8-22*p)*amplitude}px,0) rotate(${-9+12*p}deg) rotateY(${(-7+7*p)*amplitude}deg)`;
      far.style.transform=`translate3d(${(48-67*p)*amplitude}px,${(-6+12*p)*amplitude}px,-60px) rotate(${14-24*p}deg)`;
      near.style.transform=`translate3d(${(21-8*p)*amplitude}px,${(-11+16*p)*amplitude}px,-30px) rotate(${7-13*p}deg)`;
      tab.style.transform=`translate3d(${12*p*amplitude}px,${(20-42*p)*amplitude}px,0) rotate(${12-18*p}deg)`;
      arch.style.transform=`translate3d(${-42*p*amplitude}px,${-48*p*amplitude}px,0) rotate(${-18+6*p}deg)`;
    }else{
      [paper,far,near,tab,arch].forEach(e=>e.style.removeProperty('transform'));
    }
    if(index!==active){
      const direction=index>active?1:-1;
      [copies,panels].forEach((list,group)=>list.forEach((el,i)=>{
        const chosen=i===index;
        el.getAnimations().forEach(animation=>animation.cancel());
        el.style.opacity=chosen?'1':'0';
        el.style.transform='none';
        el.setAttribute('aria-hidden',String(!chosen));el.inert=!chosen;
        if(chosen&&!reduce.matches){
          const vector=group===0?`translate3d(0,${direction*24}px,0)`:`translate3d(${direction*33}px,0,0)`;
          el.animate([{opacity:0,transform:vector},{opacity:1,transform:'translate3d(0,0,0)'}],{duration:420,easing:'cubic-bezier(.2,.7,.3,1)'});
        }
      }));
      steps.forEach((b,i)=>b.setAttribute('aria-pressed',String(i===index)));
      active=index;
    }
  }
  function requestPaint(){if(!frame&&!home.hidden)frame=requestAnimationFrame(paint);}
  window.addEventListener('scroll',requestPaint,{passive:true});
  window.addEventListener('resize',()=>{measure();requestPaint();},{passive:true});
  reduce.addEventListener('change',()=>{measure();active=-1;requestPaint();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&frame){cancelAnimationFrame(frame);frame=0;}else requestPaint();});
  new ResizeObserver(()=>{measure();requestPaint();}).observe(journey);
  new MutationObserver(()=>{if(!home.hidden){active=-1;measure();requestPaint();}}).observe(home,{attributes:true,attributeFilter:['hidden']});
  root.addEventListener('click',event=>{
    const button=event.target.closest('[data-journey-step]');if(!button)return;
    measure();const anchors=[.08,.46,.82];
    window.scrollTo({top:Math.max(0,start+travel*anchors[Number(button.dataset.journeyStep)]),behavior:reduce.matches?'instant':'smooth'});
  });
  measure();requestPaint();
})();
