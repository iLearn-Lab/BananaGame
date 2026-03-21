/**
 * ========================================
 * 增强打字机效果 (Enhanced Typewriter Effect)
 * ========================================
 */

(function() {
    'use strict';
    
    console.log('⌨️ [打字机] 增强模块已加载');
    
    // ========================================
    // 配置
    // ========================================
    
    const Config = {
        defaultSpeed: 30,        // 默认打字速度（毫秒/字）
        fastSpeed: 15,          // 快速打字速度
        slowSpeed: 50,           // 慢速打字速度
        paragraphDelay: 500,    // 段落间停顿
        punctuationDelay: 150,   // 标点符号停顿
        smoothMode: true         // 是否使用平滑效果
    };
    
    // ========================================
    // 主打字机类
    // ========================================
    
    class Typewriter {
        constructor(element, options = {}) {
            this.element = element;
            this.text = '';
            this.speed = options.speed || Config.defaultSpeed;
            this.onComplete = options.onComplete || null;
            this.onChar = options.onChar || null;
            this.isRunning = false;
            this.isPaused = false;
            this.currentIndex = 0;
            this.timeoutId = null;
            
            // 状态
            this.state = {
                charCount: 0,
                startTime: null,
                endTime: null
            };
        }
        
        /**
         * 设置文本
         */
        setText(text) {
            this.text = text;
            this.currentIndex = 0;
            return this;
        }
        
        /**
         * 设置打字速度
         */
        setSpeed(speed) {
            this.speed = speed;
            return this;
        }
        
        /**
         * 开始打字
         */
        start() {
            if (this.isRunning) return this;
            
            this.isRunning = true;
            this.isPaused = false;
            this.state.startTime = Date.now();
            this.state.charCount = 0;
            
            // 添加类
            this.element.classList.add('typewriter-active');
            this.element.textContent = '';
            
            // 开始打字
            this.typeNext();
            
            return this;
        }
        
        /**
         * 打字单个字符
         */
        typeNext() {
            if (!this.isRunning || this.isPaused) return;
            
            if (this.currentIndex >= this.text.length) {
                this.complete();
                return;
            }
            
            const char = this.text[this.currentIndex];
            this.element.textContent += char;
            this.currentIndex++;
            this.state.charCount++;
            
            // 回调
            if (this.onChar) {
                this.onChar(char, this.currentIndex, this.text.length);
            }
            
            // 计算下一个延迟
            let delay = this.speed;
            
            // 标点符号停顿
            if ('。！？；：,!?;:'。includes(char)) {
                delay += Config.punctuationDelay;
            }
            
            // 换行停顿
            if (char === '\n') {
                delay += Config.paragraphDelay;
            }
            
            this.timeoutId = setTimeout(() => this.typeNext(), delay);
        }
        
        /**
         * 暂停
         */
        pause() {
            if (!this.isRunning) return this;
            
            this.isPaused = true;
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
            }
            
            this.element.classList.remove('typewriter-active');
            return this;
        }
        
        /**
         * 继续
         */
        resume() {
            if (!this.isRunning || !this.isPaused) return this;
            
            this.isPaused = false;
            this.element.classList.add('typewriter-active');
            this.typeNext();
            return this;
        }
        
        /**
         * 停止
         */
        stop() {
            this.isRunning = false;
            this.isPaused = false;
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
            }
            
            this.element.classList.remove('typewriter-active');
            this.element.classList.add('typewriter-done');
            return this;
        }
        
        /**
         * 完成
         */
        complete() {
            this.isRunning = false;
            this.state.endTime = Date.now();
            
            this.element.classList.remove('typewriter-active');
            this.element.classList.add('typewriter-done');
            
            // 移除光标
            this.element.style.borderRight = 'none';
            
            if (this.onComplete) {
                const stats = {
                    charCount: this.state.charCount,
                    duration: this.state.endTime - this.state.startTime,
                    avgSpeed: this.state.charCount / ((this.state.endTime - this.state.startTime) / 1000)
                };
                this.onComplete(stats);
            }
            
            // 屏幕阅读器公告
            if (window.announceToScreenReader) {
                window.announceToScreenReader('文本显示完成');
            }
        }
        
        /**
         * 跳过（直接显示全部）
         */
        skip() {
            if (!this.isRunning) return this;
            
            this.element.textContent = this.text;
            this.complete();
        }
    }
    
    // ========================================
    // 平滑打字机（使用 requestAnimationFrame）
    // ========================================
    
    class SmoothTypewriter {
        constructor(element, options = {}) {
            this.element = element;
            this.text = '';
            this.speed = options.speed || Config.defaultSpeed;
            this.onComplete = options.onComplete || null;
            this.isRunning = false;
            this.progress = 0; // 0-1
            this.animationId = null;
        }
        
        setText(text) {
            this.text = text;
            return this;
        }
        
        setSpeed(speed) {
            this.speed = speed;
            return this;
        }
        
        start() {
            if (this.isRunning) return this;
            
            this.isRunning = true;
            this.progress = 0;
            this.startTime = performance.now();
            this.element.textContent = '';
            
            this.animate();
            return this;
        }
        
        animate() {
            if (!this.isRunning) return;
            
            const elapsed = performance.now() - this.startTime;
            const totalDuration = this.text.length * this.speed;
            this.progress = Math.min(elapsed / totalDuration, 1);
            
            // 计算显示到第几个字符
            const charCount = Math.floor(this.progress * this.text.length);
            this.element.textContent = this.text.substring(0, charCount);
            
            if (this.progress < 1) {
                this.animationId = requestAnimationFrame(() => this.animate());
            } else {
                this.complete();
            }
        }
        
        skip() {
            if (!this.isRunning) return;
            
            this.isRunning = false;
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
            }
            
            this.element.textContent = this.text;
            this.complete();
        }
        
        complete() {
            this.isRunning = false;
            if (this.onComplete) {
                this.onComplete();
            }
        }
    }
    
    // ========================================
    // 逐字淡入效果
    // ========================================
    
    class FadeInTypewriter {
        constructor(element, options = {}) {
            this.element = element;
            this.text = '';
            this.charDelay = options.charDelay || 50;
            this.onComplete = options.onComplete || null;
            this.isRunning = false;
        }
        
        setText(text) {
            this.text = text;
            return this;
        }
        
        start() {
            if (this.isRunning) return;
            
            this.isRunning = true;
            this.element.innerHTML = '';
            
            // 创建字符 span
            const chars = this.text.split('');
            chars.forEach((char, index) => {
                const span = document.createElement('span');
                span.textContent = char;
                span.className = 'typewriter-char';
                span.style.animationDelay = `${index * this.charDelay}ms`;
                this.element.appendChild(span);
            });
            
            // 完成回调
            const totalDuration = chars.length * this.charDelay + 500;
            setTimeout(() => {
                this.isRunning = false;
                if (this.onComplete) {
                    this.onComplete();
                }
            }, totalDuration);
            
            return this;
        }
    }
    
    // ========================================
    // 快捷函数
    // ========================================
    
    /**
     * 打字效果（基础版）
     */
    function typeText(element, text, speed = 30) {
        return new Typewriter(element).setSpeed(speed).setText(text).start();
    }
    
    /**
     * 平滑打字效果
     */
    function smoothTypeText(element, text, speed = 30) {
        return new SmoothTypewriter(element).setSpeed(speed).setText(text).start();
    }
    
    /**
     * 淡入打字效果
     */
    function fadeInText(element, text, charDelay = 50) {
        return new FadeInTypewriter(element).setText(text).start();
    }
    
    // ========================================
    // 导出到全局
    // ========================================
    
    window.Typewriter = Typewriter;
    window.SmoothTypewriter = SmoothTypewriter;
    window.FadeInTypewriter = FadeInTypewriter;
    window.typeText = typeText;
    window.smoothTypeText = smoothTypeText;
    window.fadeInText = fadeInText;
    
    // ========================================
    // 使用示例
    // ========================================
    
    console.log('⌨️ [打字机] 使用方法:');
    console.log('  typeText(element, "文本", 30);      // 基础打字');
    console.log('  smoothTypeText(element, "文本", 30); // 平滑打字');
    console.log('  fadeInText(element, "文本", 50);     // 逐字淡入');
    console.log('');
    console.log('  const tw = new Typewriter(el);');
    console.log('  tw.setText("Hello").setSpeed(20).start();');
    console.log('  tw.skip(); // 跳过');
    console.log('  tw.pause(); // 暂停');
    console.log('  tw.resume(); // 继续');
    
})();
