/**
 * ========================================
 * 粒子背景效果 (Particle Background)
 * ========================================
 */

(function() {
    'use strict';
    
    console.log('✨ [粒子] 背景效果模块已加载');
    
    // ========================================
    // 配置
    // ========================================
    
    const Config = {
        particleCount: 30,           // 粒子数量
        particleColor: '26, 188, 156', // RGB 颜色 (品牌青色)
        particleSize: { min: 1, max: 3 },  // 粒子大小范围
        particleSpeed: { min: 0.1, max: 0.5 },  // 移动速度
        particleOpacity: { min: 0.2, max: 0.6 },  // 透明度范围
        connectDistance: 150,         // 连接线最大距离
        connectOpacity: 0.1,          // 连接线透明度
        enableConnect: true,          // 是否显示连接线
        enableMouse: true,            // 是否启用鼠标交互
        pauseOnHidden: true           // 界面隐藏时暂停
    };
    
    // ========================================
    // 粒子类
    // ========================================
    
    class Particle {
        constructor(canvas) {
            this.canvas = canvas;
            this.reset();
        }
        
        reset() {
            this.x = Math.random() * this.canvas.width;
            this.y = Math.random() * this.canvas.height;
            this.size = this.randomRange(Config.particleSize.min, Config.particleSize.max);
            this.speedX = this.randomRange(Config.particleSpeed.min, Config.particleSpeed.max) * (Math.random() > 0.5 ? 1 : -1);
            this.speedY = this.randomRange(Config.particleSpeed.min, Config.particleSpeed.max) * (Math.random() > 0.5 ? 1 : -1);
            this.opacity = this.randomRange(Config.particleOpacity.min, Config.particleOpacity.max);
            this.color = Config.particleColor;
        }
        
        randomRange(min, max) {
            return Math.random() * (max - min) + min;
        }
        
        update() {
            this.x += this.speedX;
            this.y += this.speedY;
            
            // 边界反弹
            if (this.x <= 0 || this.x >= this.canvas.width) {
                this.speedX *= -1;
            }
            if (this.y <= 0 || this.y >= this.canvas.height) {
                this.speedY *= -1;
            }
        }
        
        draw(ctx) {
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${this.color}, ${this.opacity})`;
            ctx.fill();
        }
        
        // 鼠标交互
        reactToMouse(mouseX, mouseY) {
            if (!Config.enableMouse || !mouseX || !mouseY) return;
            
            const dx = this.x - mouseX;
            const dy = this.y - mouseY;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            if (distance < 100) {
                // 远离鼠标
                const force = (100 - distance) / 100;
                this.x += dx * force * 0.02;
                this.y += dy * force * 0.02;
            }
        }
    }
    
    // ========================================
    // 粒子背景类
    // ========================================
    
    class ParticleBackground {
        constructor(options = {}) {
            // 合并配置
            this.config = { ...Config, ...options };
            
            this.canvas = null;
            this.ctx = null;
            this.particles = [];
            this.animationId = null;
            this.isRunning = false;
            this.isPaused = false;
            this.mouseX = null;
            this.mouseY = null;
            this.container = null;
        }
        
        /**
         * 初始化
         */
        init(container = document.body) {
            this.container = container;
            
            // 创建 canvas
            this.canvas = document.createElement('canvas');
            this.canvas.id = 'particle-canvas';
            this.canvas.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 0;
                pointer-events: none;
                opacity: 0.6;
            `;
            
            this.ctx = this.canvas.getContext('2d');
            
            // 添加到容器
            if (typeof container.appendChild === 'function') {
                container.insertBefore(this.canvas, container.firstChild);
            } else {
                document.body.insertBefore(this.canvas, document.body.firstChild);
            }
            
            // 设置尺寸
            this.resize();
            
            // 创建粒子
            this.createParticles();
            
            // 绑定事件
            this.bindEvents();
            
            console.log('✨ [粒子] 背景已初始化');
            
            return this;
        }
        
        /**
         * 创建粒子
         */
        createParticles() {
            this.particles = [];
            for (let i = 0; i < this.config.particleCount; i++) {
                this.particles.push(new Particle(this.canvas));
            }
        }
        
        /**
         * 调整尺寸
         */
        resize() {
            if (!this.canvas) return;
            
            this.canvas.width = window.innerWidth;
            this.canvas.height = window.innerHeight;
        }
        
        /**
         * 绑定事件
         */
        bindEvents() {
            // 窗口调整大小
            window.addEventListener('resize', () => {
                this.resize();
            });
            
            // 鼠标移动
            if (this.config.enableMouse) {
                window.addEventListener('mousemove', (e) => {
                    this.mouseX = e.clientX;
                    this.mouseY = e.clientY;
                });
                
                // 鼠标离开
                window.addEventListener('mouseleave', () => {
                    this.mouseX = null;
                    this.mouseY = null;
                });
            }
        }
        
        /**
         * 绘制连接线
         */
        drawConnections() {
            if (!this.config.enableConnect) return;
            
            for (let i = 0; i < this.particles.length; i++) {
                for (let j = i + 1; j < this.particles.length; j++) {
                    const p1 = this.particles[i];
                    const p2 = this.particles[j];
                    
                    const dx = p1.x - p2.x;
                    const dy = p1.y - p2.y;
                    const distance = Math.sqrt(dx * dx + dy * dy);
                    
                    if (distance < this.config.connectDistance) {
                        const opacity = (1 - distance / this.config.connectDistance) * this.config.connectOpacity;
                        
                        this.ctx.beginPath();
                        this.ctx.moveTo(p1.x, p1.y);
                        this.ctx.lineTo(p2.x, p2.y);
                        this.ctx.strokeStyle = `rgba(${this.config.particleColor}, ${opacity})`;
                        this.ctx.lineWidth = 0.5;
                        this.ctx.stroke();
                    }
                }
            }
        }
        
        /**
         * 动画循环
         */
        animate() {
            if (!this.isRunning || this.isPaused) return;
            
            // 清空画布
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            
            // 更新和绘制粒子
            this.particles.forEach(particle => {
                particle.update();
                particle.reactToMouse(this.mouseX, this.mouseY);
                particle.draw(this.ctx);
            });
            
            // 绘制连接线
            this.drawConnections();
            
            // 继续动画
            this.animationId = requestAnimationFrame(() => this.animate());
        }
        
        /**
         * 开始
         */
        start() {
            if (this.isRunning) return this;
            
            this.isRunning = true;
            this.isPaused = false;
            this.animate();
            
            console.log('✨ [粒子] 背景已开始');
            
            return this;
        }
        
        /**
         * 暂停
         */
        pause() {
            this.isPaused = true;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
            }
            
            console.log('✨ [粒子] 背景已暂停');
            
            return this;
        }
        
        /**
         * 继续
         */
        resume() {
            if (!this.isRunning) {
                this.start();
            } else {
                this.isPaused = false;
                this.animate();
            }
            
            return this;
        }
        
        /**
         * 停止
         */
        stop() {
            this.isRunning = false;
            this.isPaused = false;
            
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
            }
            
            // 清空画布
            if (this.ctx) {
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            }
            
            console.log('✨ [粒子] 背景已停止');
            
            return this;
        }
        
        /**
         * 销毁
         */
        destroy() {
            this.stop();
            
            if (this.canvas && this.canvas.parentNode) {
                this.canvas.parentNode.removeChild(this.canvas);
            }
            
            this.particles = [];
            this.canvas = null;
            this.ctx = null;
            
            console.log('✨ [粒子] 背景已销毁');
        }
        
        /**
         * 更新配置
         */
        updateConfig(options) {
            this.config = { ...this.config, ...options };
            
            // 如果更改了粒子数量，重新创建
            if (options.particleCount && options.particleCount !== this.particles.length) {
                this.createParticles();
            }
            
            return this;
        }
    }
    
    // ========================================
    // 星光效果（轻量版）
    // ========================================
    
    class StarField {
        constructor(options = {}) {
            this.config = {
                starCount: 50,
                starColor: '255, 255, 255',
                twinkleSpeed: 0.02,
                ...options
            };
            
            this.canvas = null;
            this.ctx = null;
            this.stars = [];
            this.isRunning = false;
        }
        
        init() {
            this.canvas = document.createElement('canvas');
            this.canvas.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 0;
                pointer-events: none;
            `;
            
            this.ctx = this.canvas.getContext('2d');
            document.body.insertBefore(this.canvas, document.body.firstChild);
            
            this.resize();
            this.createStars();
            this.bindEvents();
            
            return this;
        }
        
        resize() {
            if (!this.canvas) return;
            this.canvas.width = window.innerWidth;
            this.canvas.height = window.innerHeight;
        }
        
        createStars() {
            this.stars = [];
            for (let i = 0; i < this.config.starCount; i++) {
                this.stars.push({
                    x: Math.random() * this.canvas.width,
                    y: Math.random() * this.canvas.height,
                    size: Math.random() * 1.5 + 0.5,
                    opacity: Math.random(),
                    speed: Math.random() * 0.02 + 0.005
                });
            }
        }
        
        bindEvents() {
            window.addEventListener('resize', () => this.resize());
        }
        
        animate() {
            if (!this.isRunning) return;
            
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            
            this.stars.forEach(star => {
                // 闪烁
                star.opacity += Math.sin(Date.now() * star.speed) * 0.01;
                star.opacity = Math.max(0.1, Math.min(1, star.opacity));
                
                // 绘制
                this.ctx.beginPath();
                this.ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
                this.ctx.fillStyle = `rgba(${this.config.starColor}, ${star.opacity})`;
                this.ctx.fill();
            });
            
            requestAnimationFrame(() => this.animate());
        }
        
        start() {
            this.isRunning = true;
            this.animate();
            return this;
        }
        
        stop() {
            this.isRunning = false;
            if (this.canvas) {
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            }
            return this;
        }
        
        destroy() {
            this.stop();
            if (this.canvas && this.canvas.parentNode) {
                this.canvas.parentNode.removeChild(this.canvas);
            }
        }
    }
    
    // ========================================
    // 浮动气泡效果
    // ========================================
    
    class BubbleEffect {
        constructor(options = {}) {
            this.config = {
                bubbleCount: 15,
                bubbleColor: '26, 188, 156',
                minSize: 5,
                maxSize: 20,
                speed: 0.5,
                ...options
            };
            
            this.canvas = null;
            this.ctx = null;
            this.bubbles = [];
            this.isRunning = false;
        }
        
        init() {
            this.canvas = document.createElement('canvas');
            this.canvas.style.cssText = `
                position: fixed;
                bottom: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 0;
                pointer-events: none;
                opacity: 0.4;
            `;
            
            this.ctx = this.canvas.getContext('2d');
            document.body.insertBefore(this.canvas, document.body.firstChild);
            
            this.resize();
            this.createBubbles();
            window.addEventListener('resize', () => this.resize());
            
            return this;
        }
        
        resize() {
            if (!this.canvas) return;
            this.canvas.width = window.innerWidth;
            this.canvas.height = window.innerHeight;
        }
        
        createBubbles() {
            this.bubbles = [];
            for (let i = 0; i < this.config.bubbleCount; i++) {
                this.bubbles.push(this.createBubble());
            }
        }
        
        createBubble() {
            return {
                x: Math.random() * this.canvas.width,
                y: this.canvas.height + Math.random() * 100,
                size: Math.random() * (this.config.maxSize - this.config.minSize) + this.config.minSize,
                speedY: Math.random() * this.config.speed + 0.2,
                speedX: (Math.random() - 0.5) * 0.5,
                opacity: Math.random() * 0.3 + 0.1
            };
        }
        
        animate() {
            if (!this.isRunning) return;
            
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            
            this.bubbles.forEach((bubble, index) => {
                // 上升
                bubble.y -= bubble.speedY;
                bubble.x += bubble.speedX;
                
                // 左右微摆
                bubble.x += Math.sin(Date.now() * 0.001 + index) * 0.3;
                
                // 绘制气泡
                this.ctx.beginPath();
                this.ctx.arc(bubble.x, bubble.y, bubble.size, 0, Math.PI * 2);
                this.ctx.strokeStyle = `rgba(${this.config.bubbleColor}, ${bubble.opacity})`;
                this.ctx.lineWidth = 1;
                this.ctx.stroke();
                
                // 重新到底部
                if (bubble.y < -bubble.size * 2) {
                    this.bubbles[index] = this.createBubble();
                }
            });
            
            requestAnimationFrame(() => this.animate());
        }
        
        start() {
            this.isRunning = true;
            this.animate();
            return this;
        }
        
        stop() {
            this.isRunning = false;
            return this;
        }
    }
    
    // ========================================
    // 全局实例管理
    // ========================================
    
    const ParticleManager = {
        instances: {},
        
        /**
         * 创建粒子背景
         */
        create(type = 'particles', options = {}) {
            let instance;
            
            switch(type) {
                case 'particles':
                    instance = new ParticleBackground(options);
                    break;
                case 'stars':
                    instance = new StarField(options);
                    break;
                case 'bubbles':
                    instance = new BubbleEffect(options);
                    break;
                default:
                    console.warn(`✨ [粒子] 未知类型: ${type}`);
                    return null;
            }
            
            instance.init();
            instance.start();
            
            this.instances[type] = instance;
            
            return instance;
        },
        
        /**
         * 获取实例
         */
        get(type = 'particles') {
            return this.instances[type];
        },
        
        /**
         * 销毁实例
         */
        destroy(type = 'particles') {
            if (this.instances[type]) {
                this.instances[type].destroy();
                delete this.instances[type];
            }
        },
        
        /**
         * 销毁全部
         */
        destroyAll() {
            Object.keys(this.instances).forEach(type => {
                this.destroy(type);
            });
        },
        
        /**
         * 暂停全部
         */
        pauseAll() {
            Object.values(this.instances).forEach(instance => {
                instance.pause();
            });
        },
        
        /**
         * 继续全部
         */
        resumeAll() {
            Object.values(this.instances).forEach(instance => {
                instance.resume();
            });
        }
    };
    
    // ========================================
    // 导出到全局
    // ========================================
    
    window.ParticleBackground = ParticleBackground;
    window.StarField = StarField;
    window.BubbleEffect = BubbleEffect;
    window.ParticleManager = ParticleManager;
    
    // 快捷方法
    window.createParticles = (options) => ParticleManager.create('particles', options);
    window.createStarField = (options) => ParticleManager.create('stars', options);
    window.createBubbles = (options) => ParticleManager.create('bubbles', options);
    window.destroyParticles = () => ParticleManager.destroyAll();
    
    console.log('✨ [粒子] 使用方法:');
    console.log('  createParticles();           // 创建粒子背景');
    console.log('  createStarField();         // 创建星场');
    console.log('  createBubbles();           // 创建气泡效果');
    console.log('  ParticleManager.pauseAll(); // 暂停所有');
    console.log('  ParticleManager.resumeAll(); // 继续所有');
    console.log('  ParticleManager.destroyAll(); // 销毁所有');
    
})();
