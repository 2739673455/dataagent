import{g as p,c as w,r as a,j as e,R as y}from"./index-l3uEp8fe.js";import{A as j}from"./arrow-left-DB93eW7A.js";const N=[["path",{d:"M9 10h.01",key:"qbtxuw"}],["path",{d:"M15 10h.01",key:"1qmjsl"}],["path",{d:"M12 2a8 8 0 0 0-8 8v12l3-3 2.5 2.5L12 19l2.5 2.5L17 19l3 3V10a8 8 0 0 0-8-8z",key:"uwwb07"}]],k=p("ghost",N);const M=[["path",{d:"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",key:"5wwlr5"}],["path",{d:"M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",key:"r6nss1"}]],z=p("house",M);function P(){const l=w(),d=a.useRef(null),[c,b]=a.useState({x:0,y:0}),r=a.useRef([]),x="inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 h-10 px-8";return a.useEffect(()=>{const n=d.current;if(!n)return;const s=n.getContext("2d");if(!s)return;const i=()=>{n.width=window.innerWidth,n.height=window.innerHeight};i(),window.addEventListener("resize",i),r.current=Array.from({length:50},()=>({x:Math.random()*n.width,y:Math.random()*n.height,sx:(Math.random()-.5)*.5,sy:(Math.random()-.5)*.5,size:Math.random()*2+1,opacity:Math.random()*.5+.2}));let h;const m=()=>{s.fillStyle="rgba(15, 23, 42, 0.1)",s.fillRect(0,0,n.width,n.height),r.current.forEach(t=>{t.x+=t.sx,t.y+=t.sy,(t.x<0||t.x>n.width)&&(t.sx*=-1),(t.y<0||t.y>n.height)&&(t.sy*=-1),s.beginPath(),s.arc(t.x,t.y,t.size,0,Math.PI*2),s.fillStyle=`rgba(99, 102, 241, ${t.opacity})`,s.fill()}),r.current.forEach((t,v)=>{r.current.slice(v+1).forEach(o=>{const u=t.x-o.x,g=t.y-o.y,f=Math.sqrt(u*u+g*g);f<150&&(s.beginPath(),s.moveTo(t.x,t.y),s.lineTo(o.x,o.y),s.strokeStyle=`rgba(99, 102, 241, ${.1*(1-f/150)})`,s.stroke())})}),h=requestAnimationFrame(m)};return m(),()=>{window.removeEventListener("resize",i),cancelAnimationFrame(h)}},[]),a.useEffect(()=>{const n=s=>{b({x:(s.clientX-window.innerWidth/2)/50,y:(s.clientY-window.innerHeight/2)/50})};return window.addEventListener("mousemove",n),()=>window.removeEventListener("mousemove",n)},[]),e.jsxs("div",{className:"relative min-h-screen overflow-hidden bg-slate-950 flex items-center justify-center",children:[e.jsx("canvas",{ref:d,className:"absolute inset-0 pointer-events-none"}),e.jsxs("div",{className:"absolute inset-0 overflow-hidden pointer-events-none",children:[e.jsx("div",{className:"absolute top-1/4 left-1/4 w-96 h-96 bg-indigo-500/20 rounded-full blur-[120px] animate-pulse"}),e.jsx("div",{className:"absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-[120px] animate-pulse delay-1000"}),e.jsx("div",{className:"absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-blue-500/10 rounded-full blur-[150px]"})]}),e.jsx("div",{className:"absolute inset-0 opacity-[0.03] pointer-events-none",style:{backgroundImage:`
						linear-gradient(rgba(99, 102, 241, 0.5) 1px, transparent 1px),
						linear-gradient(90deg, rgba(99, 102, 241, 0.5) 1px, transparent 1px)
					`,backgroundSize:"50px 50px"}}),e.jsxs("div",{className:"relative z-10 text-center px-4",style:{transform:`translate(${c.x}px, ${c.y}px)`,transition:"transform 0.3s ease-out"},children:[e.jsxs("div",{className:"relative mb-8",children:[e.jsx("h1",{className:"text-[150px] md:text-[200px] font-black leading-none tracking-tighter select-none",style:{textShadow:`
								-2px -2px 0 #6366f1,
								2px 2px 0 #a855f7,
								4px 4px 20px rgba(99, 102, 241, 0.5),
								-4px -4px 20px rgba(168, 85, 247, 0.5)
							`,background:"linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #3b82f6 100%)",WebkitBackgroundClip:"text",WebkitTextFillColor:"transparent",backgroundClip:"text",animation:"glitch 3s infinite"},children:"404"}),e.jsx("div",{className:"absolute -top-8 -right-8 md:-top-8 md:-right-8 text-indigo-400 animate-bounce",style:{animationDuration:"2s"},children:e.jsx(k,{className:"w-10 h-10 md:w-12 md:h-12 opacity-80"})})," "]}),e.jsx("div",{className:"space-y-4 mb-10",children:e.jsx("h2",{className:"text-2xl md:text-4xl font-bold text-white tracking-wide",children:e.jsx("span",{className:"inline-flex items-center gap-2",children:"页面不存在"})})}),e.jsxs("div",{className:"flex flex-col sm:flex-row gap-4 justify-center items-center",children:[e.jsxs("button",{type:"button",className:`${x} group relative px-8 py-6 text-base font-semibold text-white bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 border-0 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 transition-all duration-300 hover:scale-105`,onClick:()=>l(y.home),children:[e.jsx(z,{className:"w-5 h-5 mr-2 relative z-10 transition-transform duration-300 group-hover:scale-110"}),e.jsx("span",{className:"relative z-10",children:"返回首页"})]}),e.jsxs("button",{type:"button",className:`${x} group px-8 py-6 text-base font-semibold border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground border-2 border-slate-700 bg-slate-900/50 text-slate-300 hover:bg-slate-800 hover:border-slate-600 hover:text-white transition-all duration-300 hover:scale-105 backdrop-blur-sm`,onClick:()=>l(-1),children:[e.jsx(j,{className:"w-5 h-5 mr-2 group-hover:-translate-x-1 transition-transform"}),"返回上一页"]})]})]}),e.jsx("style",{children:`
				@keyframes glitch {
					0%, 90%, 100% {
						transform: translate(0);
						filter: hue-rotate(0deg);
					}
					92% {
						transform: translate(-2px, 2px);
						filter: hue-rotate(90deg);
					}
					94% {
						transform: translate(2px, -2px);
						filter: hue-rotate(180deg);
					}
					96% {
						transform: translate(-2px, -2px);
						filter: hue-rotate(270deg);
					}
					98% {
						transform: translate(2px, 2px);
						filter: hue-rotate(360deg);
					}
				}
			`})]})}export{P as default};
