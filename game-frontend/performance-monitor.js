/**
 * ========================================
 * 性能监控工具 (Performance Monitor)
 * ========================================
 * 
 * 用于检测和优化动画性能
 */

(function() {
    'use strict';
    
    console.log('📊 [性能监控] 模块已加载');
    
    // ========================================
    // 配置
    // ========================================
    
    const Config = {
        enabled: true,              // 是否启用监控
        showFPS: true,               // 显示 FPS
        showWarnings: true,          // 显示警告
        targetFPS: 60,               // 目标帧率
        warnThreshold: 30,            // 警告阈值（低于此帧率警告）
        logPerformance: false,       // 是否记录性能数据
        panelPosition: 'top-right',  // 面板位置
        autoDisableOnLowEnd: true    // 低性能设备自动禁用
    };
    
    // ========================================
    // 性能监控类
    // ========================================
    
    class PerformanceMonitor {
        constructor() {
            this.fps = 0;
            this.frames = 0;
            this.lastTime = performance.now();
            this.running = false;
            this.panel = null;
            this.stats = {
                avgFPS: 0,
                minFPS: 60,
                maxFPS: 0,
                droppedFrames: 0,
                totalFrames: 0
            };
        }
        
        /**
         * 初始化
         */
        init() {
            // 检测是否为低性能设备
            if (Config.autoDisableOnLowEnd && this.isLowEndDevice()) {
                console.log('📊 [性能监控] 检测到低性能设备，已自动禁用');
                return;
            }
            
            // 创建面板
            this.createPanel();
            
            // 开始监控
            this.start();
            
            console.log('📊 [性能监控] 已启动');
        }
        
        /**
         * 检测低性能设备
         */
        isLowEndDevice() {
            // 检测移动设备
            const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
            
            // 检测低内存
            const lowMemory = navigator.deviceMemory && navigator.deviceMemory < 4;
            
            // 检测小屏幕
            const smallScreen = window.innerWidth < 768;
            
            return isMobile && (lowMemory || smallScreen);
        }
        
        /**
         * 创建监控面板
         */
        createPanel() {
            if (!Config.showFPS) return;
            
            this.panel = document.createElement('div');
            this.panel.id = 'performance-monitor';
            this.panel.style.cssText = `
                position: fixed;
                top: 10px;
                right: 10px;
                background: rgba(0, 0, 0, 0.8);
                color: #fff;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 12px;
                font-family: monospace;
                z-index: 99999;
                min-width: 100px;
                pointer-events: none;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.1);
            `;
            
            document.body.appendChild(this.panel);
        }
        
        /**
         * 更新面板显示
         */
        updatePanel() {
            if (!this.panel) return;
            
            const fpsColor = this.fps >= Config.targetFPS ? '#2ecc71' : 
                            this.fps >= Config.warnThreshold ? '#f39c12' : '#e74c3c';
            
            this.panel.innerHTML = `
                <div style="color: ${fpsColor}; font-size: 16px; font-weight: bold;">
                    ${this.fps} FPS
                </div>
                <div style="color: #7f8c8d; margin-top: 4px;">
                    Min: ${this.stats.minFPS} | Max: ${this.stats.maxFPS}
                </div>
            `;
        }
        
        /**
         * 帧循环
         */
        loop() {
            if (!this.running) return;
            
            this.frames++;
            const currentTime = performance.now();
            const delta = currentTime - this.lastTime;
            
            // 每秒更新一次
            if (delta >= 1000) {
                this.fps = Math.round((this.frames * 1000) / delta);
                this.stats.totalFrames += this.frames;
                this.stats.minFPS = Math.min(this.stats.minFPS, this.fps);
                this.stats.maxFPS = Math.max(this.stats.maxFPS, this.fps);
                this.stats.avgFPS = Math.round(this.stats.totalFrames / (currentTime - performance.now()));
                
                // 检测掉帧
                if (this.fps < Config.warnThreshold) {
                    this.stats.droppedFrames += this.frames;
                    if (Config.showWarnings) {
                        console.warn(`📊 [性能监控] FPS 过低: ${this.fps} FPS`);
                    }
                }
                
                this.frames = 0;
                this.lastTime = currentTime;
                
                this.updatePanel();
            }
            
            requestAnimationFrame(() => this.loop());
        }
        
        /**
         * 开始监控
         */
        start() {
            if (this.running) return;
            
            this.running = true;
            this.lastTime = performance.now();
            this.loop();
        }
        
        /**
         * 停止监控
         */
        stop() {
            this.running = false;
        }
        
        /**
         * 获取统计信息
         */
        getStats() {
            return { ...this.stats, currentFPS: this.fps };
        }
        
        /**
         * 输出报告
         */
        report() {
            const stats = this.getStats();
            console.log(`
📊 [性能监控] 报告:
-------------------
当前 FPS: ${stats.currentFPS}
平均 FPS: ${stats.avgFPS}
最低 FPS: ${stats.minFPS}
最高 FPS: ${stats.maxFPS}
掉帧次数: ${stats.droppedFrames}
总帧数: ${stats.totalFrames}
-------------------
            `);
        }
        
        /**
         * 销毁
         */
        destroy() {
            this.stop();
            if (this.panel && this.panel.parentNode) {
                this.panel.parentNode.removeChild(this.panel);
            }
            this.panel = null;
        }
    }
    
    // ========================================
    // 动画性能检测
    // ========================================
    
    class AnimationChecker {
        constructor() {
            this.slowAnimations = [];
        }
        
        /**
         * 检查页面上的动画
         */
        check() {
            this.slowAnimations = [];
            
            const elements = document.querySelectorAll('*');
            let checkedCount = 0;
            
            elements.forEach(el => {
                const styles = getComputedStyle(el);
                
                // 检查动画时长
                const animationDuration = this.parseDuration(styles.animationDuration);
                const transitionDuration = this.parseDuration(styles.transitionDuration);
                
                if (animationDuration > 2 || transitionDuration > 1) {
                    this.slowAnimations.push({
                        element: el,
                        tag: el.tagName,
                        animationDuration,
                        transitionDuration,
                        hasAnimation: styles.animationName !== 'none',
                        hasTransition: styles.transitionProperty !== 'all'
                    });
                }
                
                checkedCount++;
            });
            
            console.log(`📊 [动画检测] 检查了 ${checkedCount} 个元素`);
            
            if (this.slowAnimations.length > 0) {
                console.warn(`📊 [动画检测] 发现 ${this.slowAnimations.length} 个可能影响性能的动画`);
                
                if (Config.showWarnings) {
                    this.slowAnimations.slice(0, 5).forEach((anim, i) => {
                        console.warn(`  ${i + 1}. ${anim.tag} - 动画: ${anim.animationDuration}s, 过渡: ${anim.transitionDuration}s`);
                    });
                }
            }
            
            return this.slowAnimations;
        }
        
        /**
         * 解析时长
         */
        parseDuration(value) {
            if (value === '0s' || value === '0') return 0;
            const num = parseFloat(value);
            return isNaN(num) ? 0 : num;
        }
        
        /**
         * 标记慢动画元素（红色边框）
         */
        highlightSlowAnimations() {
            this.check().forEach(anim => {
                anim.element.style.outline = '2px solid red';
                anim.element.style.outlineOffset = '2px';
            });
        }
        
        /**
         * 清除标记
         */
        clearHighlights() {
            document.querySelectorAll('*').forEach(el => {
                el.style.outline = '';
                el.style.outlineOffset = '';
            });
        }
    }
    
    // ========================================
    // 内存监控
    // ========================================
    
    class MemoryMonitor {
        /**
         * 获取内存使用情况
         */
        getMemoryUsage() {
            if (performance.memory) {
                return {
                    used: (performance.memory.usedJSHeapSize / 1048576).toFixed(2) + ' MB',
                    total: (performance.memory.totalJSHeapSize / 1048576).toFixed(2) + ' MB',
                    limit: (performance.memory.jsHeapSizeLimit / 1048576).toFixed(2) + ' MB',
                    percentage: ((performance.memory.usedJSHeapSize / performance.memory.jsHeapSizeLimit) * 100).toFixed(1) + '%'
                };
            }
            return null;
        }
        
        /**
         * 输出内存报告
         */
        report() {
            const memory = this.getMemoryUsage();
            if (memory) {
                console.log(`
📊 [内存监控] 报告:
-------------------
已使用: ${memory.used}
总大小: ${memory.total}
内存上限: ${memory.limit}
使用率: ${memory.percentage}
-------------------
                `);
            } else {
                console.log('📊 [内存监控] 不支持 memory API');
            }
            return memory;
        }
    }
    
    // ========================================
    // 快速调试命令
    // ========================================
    
    // 创建全局调试对象
    window.PerformanceMonitor = PerformanceMonitor;
    window.AnimationChecker = AnimationChecker;
    window.MemoryMonitor = MemoryMonitor;
    
    // 快捷方法
    window.perf = {
        // 启动性能监控
        start: () => {
            if (!window.perfMonitor) {
                window.perfMonitor = new PerformanceMonitor();
                window.perfMonitor.init();
            }
            return window.perfMonitor;
        },
        
        // 停止性能监控
        stop: () => {
            if (window.perfMonitor) {
                window.perfMonitor.stop();
            }
        },
        
        // 获取统计
        stats: () => {
            return window.perfMonitor ? window.perfMonitor.getStats() : null;
        },
        
        // 输出报告
        report: () => {
            if (window.perfMonitor) {
                window.perfMonitor.report();
            }
            new MemoryMonitor().report();
        },
        
        // 检查动画
        checkAnimations: () => {
            const checker = new AnimationChecker();
            return checker.check();
        },
        
        // 标记慢动画
        highlight: () => {
            const checker = new AnimationChecker();
            checker.highlightSlowAnimations();
        },
        
        // 清除标记
        clearHighlights: () => {
            const checker = new AnimationChecker();
            checker.clearHighlights();
        },
        
        // 内存报告
        memory: () => {
            return new MemoryMonitor().report();
        },
        
        // 禁用所有动画
        disableAnimations: () => {
            document.body.style.transition = 'none';
            document.body.style.animation = 'none';
            console.log('📊 [调试] 已禁用所有动画');
        },
        
        // 启用所有动画
        enableAnimations: () => {
            document.body.style.transition = '';
            document.body.style.animation = '';
            console.log('📊 [调试] 已启用所有动画');
        }
    };
    
    // 自动启动（可选，取消注释即可）
    // window.addEventListener('load', () => {
    //     setTimeout(() => perf.start(), 1000);
    // });
    
    console.log('📊 [性能监控] 使用方法:');
    console.log('  perf.start()           // 启动性能监控');
    console.log('  perf.stop()            // 停止监控');
    console.log('  perf.stats()           // 获取统计');
    console.log('  perf.report()          // 输出完整报告');
    console.log('  perf.checkAnimations()  // 检查慢动画');
    console.log('  perf.highlight()       // 标记慢动画元素');
    console.log('  perf.memory()          // 内存使用情况');
    console.log('  perf.disableAnimations() // 禁用动画');
    console.log('  perf.enableAnimations()  // 启用动画');
    
})();
