import { ArrowLeft, Ghost, Home } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ROUTE_PATHS } from "@/shared/config/settings";

interface Particle {
  x: number;
  y: number;
  sx: number;
  sy: number;
  size: number;
  opacity: number;
}

export default function NotFound() {
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const particlesRef = useRef<Particle[]>([]);
  const baseButtonClassName =
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 h-10 px-8";

  // 粒子背景动画
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resizeCanvas = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

    // 初始化粒子
    const initParticles = () => {
      particlesRef.current = Array.from({ length: 50 }, () => ({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        sx: (Math.random() - 0.5) * 0.5,
        sy: (Math.random() - 0.5) * 0.5,
        size: Math.random() * 2 + 1,
        opacity: Math.random() * 0.5 + 0.2,
      }));
    };
    initParticles();

    let animationId: number;
    const animate = () => {
      ctx.fillStyle = "rgba(15, 23, 42, 0.1)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      particlesRef.current.forEach((particle) => {
        particle.x += particle.sx;
        particle.y += particle.sy;

        if (particle.x < 0 || particle.x > canvas.width) particle.sx *= -1;
        if (particle.y < 0 || particle.y > canvas.height) particle.sy *= -1;

        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(99, 102, 241, ${particle.opacity})`;
        ctx.fill();
      });

      // 连接临近粒子
      particlesRef.current.forEach((p1, i) => {
        particlesRef.current.slice(i + 1).forEach((p2) => {
          const dx = p1.x - p2.x;
          const dy = p1.y - p2.y;
          const distance = Math.sqrt(dx * dx + dy * dy);

          if (distance < 150) {
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.strokeStyle = `rgba(99, 102, 241, ${0.1 * (1 - distance / 150)})`;
            ctx.stroke();
          }
        });
      });

      animationId = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      window.removeEventListener("resize", resizeCanvas);
      cancelAnimationFrame(animationId);
    };
  }, []);

  // 鼠标视差效果
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      setMousePos({
        x: (e.clientX - window.innerWidth / 2) / 50,
        y: (e.clientY - window.innerHeight / 2) / 50,
      });
    };
    window.addEventListener("mousemove", handleMouseMove);
    return () => window.removeEventListener("mousemove", handleMouseMove);
  }, []);

  return (
    <div className="relative min-h-screen overflow-hidden bg-slate-950 flex items-center justify-center">
      {/* 粒子画布背景 */}
      <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none" />

      {/* 发光背景效果 */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-indigo-500/20 rounded-full blur-[120px] animate-pulse" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-500/20 rounded-full blur-[120px] animate-pulse delay-1000" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-blue-500/10 rounded-full blur-[150px]" />
      </div>

      {/* 网格背景 */}
      <div
        className="absolute inset-0 opacity-[0.03] pointer-events-none"
        style={{
          backgroundImage: `
						linear-gradient(rgba(99, 102, 241, 0.5) 1px, transparent 1px),
						linear-gradient(90deg, rgba(99, 102, 241, 0.5) 1px, transparent 1px)
					`,
          backgroundSize: "50px 50px",
        }}
      />

      {/* 主要内容 */}
      <div
        className="relative z-10 text-center px-4"
        style={{
          transform: `translate(${mousePos.x}px, ${mousePos.y}px)`,
          transition: "transform 0.3s ease-out",
        }}
      >
        {/* 404 故障艺术文字 */}
        <div className="relative mb-8">
          <h1
            className="text-[150px] md:text-[200px] font-black leading-none tracking-tighter select-none"
            style={{
              textShadow: `
								-2px -2px 0 #6366f1,
								2px 2px 0 #a855f7,
								4px 4px 20px rgba(99, 102, 241, 0.5),
								-4px -4px 20px rgba(168, 85, 247, 0.5)
							`,
              background: "linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #3b82f6 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
              animation: "glitch 3s infinite",
            }}
          >
            404
          </h1>
          {/* 浮动幽灵图标 */}
          <div
            className="absolute -top-8 -right-8 md:-top-8 md:-right-8 text-indigo-400 animate-bounce"
            style={{ animationDuration: "2s" }}
          >
            <Ghost className="w-10 h-10 md:w-12 md:h-12 opacity-80" />
          </div>{" "}
        </div>

        {/* 副标题 */}
        <div className="space-y-4 mb-10">
          <h2 className="text-2xl md:text-4xl font-bold text-white tracking-wide">
            <span className="inline-flex items-center gap-2">页面不存在</span>
          </h2>
        </div>

        {/* 操作按钮 */}
        <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
          <button
            type="button"
            className={`${baseButtonClassName} group relative px-8 py-6 text-base font-semibold text-white bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 border-0 shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/40 transition-all duration-300 hover:scale-105`}
            onClick={() => navigate(ROUTE_PATHS.home)}
          >
            <Home className="w-5 h-5 mr-2 relative z-10 transition-transform duration-300 group-hover:scale-110" />
            <span className="relative z-10">返回首页</span>
          </button>

          <button
            type="button"
            className={`${baseButtonClassName} group px-8 py-6 text-base font-semibold border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground border-2 border-slate-700 bg-slate-900/50 text-slate-300 hover:bg-slate-800 hover:border-slate-600 hover:text-white transition-all duration-300 hover:scale-105 backdrop-blur-sm`}
            onClick={() => navigate(-1)}
          >
            <ArrowLeft className="w-5 h-5 mr-2 group-hover:-translate-x-1 transition-transform" />
            返回上一页
          </button>
        </div>
      </div>

      {/* CSS 动画 */}
      <style>{`
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
			`}</style>
    </div>
  );
}
