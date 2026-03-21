/**
 * ========================================
 * 无障碍增强脚本 (Accessibility Enhancement)
 * ========================================
 * 
 * 为 DN 游戏前端添加完整的 ARIA 支持和键盘导航
 */

(function() {
    'use strict';
    
    console.log('♿ [无障碍] 增强脚本已加载');
    
    // ========================================
    // 1. 键盘导航增强
    // ========================================
    
    function initKeyboardNavigation() {
        // 为所有可交互元素添加键盘支持
        const interactiveElements = document.querySelectorAll(
            'button, [role="button"], [tabindex="0"]'
        );
        
        interactiveElements.forEach(el => {
            // 添加键盘事件支持
            el.addEventListener('keydown', handleKeyboardInteraction);
        });
        
        console.log('♿ [无障碍] 键盘导航已初始化');
    }
    
    function handleKeyboardInteraction(e) {
        if (e.key === 'Enter' || e.key === ' ') {
            if (!e.target.disabled) {
                e.preventDefault();
                e.target.click();
            }
        }
        
        // Escape 键关闭模态框
        if (e.key === 'Escape') {
            const modal = document.getElementById('modal');
            if (modal && !modal.classList.contains('hidden')) {
                closeModal();
            }
        }
    }
    
    // ========================================
    // 2. 加载状态增强
    // ========================================
    
    function enhanceLoadingScreen() {
        const loadingScreen = document.getElementById('loading-screen');
        const loadingStatus = document.getElementById('loading-status');
        const loadingPercent = document.getElementById('loading-percent');
        
        if (loadingScreen) {
            loadingScreen.setAttribute('role', 'status');
            loadingScreen.setAttribute('aria-live', 'polite');
            loadingScreen.setAttribute('aria-label', '游戏加载中');
        }
        
        if (loadingStatus) {
            loadingStatus.setAttribute('role', 'status');
            loadingStatus.setAttribute('aria-live', 'polite');
        }
        
        if (loadingPercent) {
            loadingPercent.setAttribute('role', 'status');
            loadingPercent.setAttribute('aria-live', 'assertive');
            loadingPercent.setAttribute('aria-label', '加载进度百分比');
        }
        
        console.log('♿ [无障碍] 加载状态已增强');
    }
    
    // ========================================
    // 3. 选项卡片增强
    // ========================================
    
    function enhanceOptionCards() {
        const optionCards = document.querySelectorAll('.option-card');
        
        optionCards.forEach((card, index) => {
            // 确保卡片可以被聚焦
            if (!card.hasAttribute('tabindex')) {
                card.setAttribute('tabindex', '0');
            }
            
            // 添加角色
            if (!card.hasAttribute('role')) {
                card.setAttribute('role', 'button');
            }
            
            // 获取选项文本并设为 aria-label
            const optionText = card.querySelector('.option-text');
            if (optionText) {
                const text = optionText.textContent.trim();
                card.setAttribute('aria-label', `选项 ${index + 1}: ${text}`);
            }
        });
        
        console.log('♿ [无障碍] 选项卡片已增强');
    }
    
    // ========================================
    // 4. 难度卡片增强
    // ========================================
    
    function enhanceDifficultyCards() {
        const difficultyCards = document.querySelectorAll('.difficulty-card');
        
        difficultyCards.forEach(card => {
            card.setAttribute('role', 'option');
            card.setAttribute('tabindex', '0');
            
            const difficulty = card.dataset.difficulty;
            card.setAttribute('aria-label', `游戏难度: ${difficulty}`);
            
            // 监听选择状态变化
            const observer = new MutationObserver(mutations => {
                mutations.forEach(mutation => {
                    if (mutation.attributeName === 'class') {
                        const isSelected = card.classList.contains('selected');
                        card.setAttribute('aria-selected', isSelected);
                    }
                });
            });
            
            observer.observe(card, { attributes: true });
        });
        
        console.log('♿ [无障碍] 难度卡片已增强');
    }
    
    // ========================================
    // 5. 基调卡片增强
    // ========================================
    
    function enhanceToneCards() {
        const toneCards = document.querySelectorAll('.tone-card');
        
        toneCards.forEach(card => {
            card.setAttribute('role', 'option');
            card.setAttribute('tabindex', '0');
            
            const toneName = card.querySelector('.tone-name');
            if (toneName) {
                card.setAttribute('aria-label', `故事基调: ${toneName.textContent}`);
            }
            
            // 监听选择状态变化
            const observer = new MutationObserver(mutations => {
                mutations.forEach(mutation => {
                    if (mutation.attributeName === 'class') {
                        const isSelected = card.classList.contains('selected');
                        card.setAttribute('aria-selected', isSelected);
                    }
                });
            });
            
            observer.observe(card, { attributes: true });
        });
        
        console.log('♿ [无障碍] 基调卡片已增强');
    }
    
    // ========================================
    // 6. 属性选择按钮增强
    // ========================================
    
    function enhanceAttributeButtons() {
        const attrOptions = document.querySelectorAll('.attr-options');
        
        attrOptions.forEach(optionGroup => {
            const attrName = optionGroup.dataset.attr;
            const buttons = optionGroup.querySelectorAll('.attr-option-btn');
            
            buttons.forEach(btn => {
                const value = btn.dataset.value;
                btn.setAttribute('role', 'radio');
                btn.setAttribute('aria-label', `${attrName}: ${value}`);
                btn.setAttribute('aria-checked', btn.classList.contains('selected') ? 'true' : 'false');
                
                // 监听选择状态变化
                btn.addEventListener('click', () => {
                    // 同一组内只有一个被选中
                    buttons.forEach(b => b.setAttribute('aria-checked', 'false'));
                    btn.setAttribute('aria-checked', 'true');
                });
            });
        });
        
        console.log('♿ [无障碍] 属性按钮已增强');
    }
    
    // ========================================
    // 7. 角色面板增强
    // ========================================
    
    function enhanceCharacterPanel() {
        const panel = document.getElementById('character-panel');
        
        if (panel) {
            panel.setAttribute('role', 'complementary');
            panel.setAttribute('aria-label', '角色状态面板');
            panel.setAttribute('aria-hidden', 'true'); // 默认隐藏，可通过按钮切换
        }
        
        console.log('♿ [无障碍] 角色面板已增强');
    }
    
    // ========================================
    // 8. 模态框增强
    // ========================================
    
    function enhanceModal() {
        const modal = document.getElementById('modal');
        const modalContent = document.getElementById('modal-content');
        const modalTitle = document.getElementById('modal-title');
        const closeBtn = modal?.querySelector('.close-modal');
        
        if (modal) {
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            
            if (modalTitle) {
                modal.setAttribute('aria-labelledby', 'modal-title');
            }
            
            // 焦点管理
            modal.addEventListener('keydown', handleModalKeyboard);
        }
        
        console.log('♿ [无障碍] 模态框已增强');
    }
    
    function handleModalKeyboard(e) {
        if (e.key === 'Tab') {
            // 确保焦点在模态框内循环
            const focusableElements = document.querySelectorAll(
                '#modal button, #modal input, #modal [tabindex="0"]'
            );
            
            if (focusableElements.length === 0) return;
            
            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];
            
            if (e.shiftKey) {
                if (document.activeElement === firstElement) {
                    e.preventDefault();
                    lastElement.focus();
                }
            } else {
                if (document.activeElement === lastElement) {
                    e.preventDefault();
                    firstElement.focus();
                }
            }
        }
    }
    
    function closeModal() {
        const modal = document.getElementById('modal');
        if (modal) {
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
        }
    }
    
    // ========================================
    // 9. 设置页面标签增强
    // ========================================
    
    function enhanceSettingTabs() {
        const navItems = document.querySelectorAll('.setting-nav .nav-item');
        const contentTabs = document.querySelectorAll('.content-tab');
        
        if (navItems.length > 0) {
            // 添加 tablist 角色
            const navContainer = navItems[0].parentElement;
            navContainer.setAttribute('role', 'tablist');
            navContainer.setAttribute('aria-label', '游戏设置分类');
            
            navItems.forEach((item, index) => {
                item.setAttribute('role', 'tab');
                item.setAttribute('tabindex', '0');
                item.setAttribute('aria-selected', item.classList.contains('active') ? 'true' : 'false');
                item.setAttribute('aria-controls', `tab-${index}`);
                
                // 内容面板关联
                const contentId = `tab-${index}`;
                const content = contentTabs[index];
                if (content) {
                    content.id = contentId;
                    content.setAttribute('role', 'tabpanel');
                }
                
                // 键盘导航
                item.addEventListener('keydown', (e) => {
                    handleTabNavigation(e, navItems, index);
                });
            });
        }
        
        console.log('♿ [无障碍] 设置标签已增强');
    }
    
    function handleTabNavigation(e, items, currentIndex) {
        let newIndex = currentIndex;
        
        switch(e.key) {
            case 'ArrowRight':
            case 'ArrowDown':
                newIndex = (currentIndex + 1) % items.length;
                break;
            case 'ArrowLeft':
            case 'ArrowUp':
                newIndex = (currentIndex - 1 + items.length) % items.length;
                break;
            case 'Home':
                newIndex = 0;
                break;
            case 'End':
                newIndex = items.length - 1;
                break;
            default:
                return;
        }
        
        e.preventDefault();
        items[newIndex].click();
        items[newIndex].focus();
    }
    
    // ========================================
    // 10. 进度条增强
    // ========================================
    
    function enhanceProgressBar() {
        const progressBar = document.querySelector('.progress-bar');
        
        if (progressBar) {
            progressBar.setAttribute('role', 'progressbar');
            progressBar.setAttribute('aria-label', '章节进度');
            progressBar.setAttribute('aria-valuemin', '0');
            progressBar.setAttribute('aria-valuemax', '100');
        }
        
        console.log('♿ [无障碍] 进度条已增强');
    }
    
    // ========================================
    // 11. 图片加载状态
    // ========================================
    
    function enhanceImageLoading() {
        const imageLoading = document.getElementById('image-loading');
        
        if (imageLoading) {
            imageLoading.setAttribute('role', 'status');
            imageLoading.setAttribute('aria-live', 'polite');
            imageLoading.setAttribute('aria-label', '场景图片加载中');
        }
        
        console.log('♿ [无障碍] 图片加载状态已增强');
    }
    
    // ========================================
    // 12. 结局界面增强
    // ========================================
    
    function enhanceEndingScreen() {
        const endingScreen = document.getElementById('ending-screen');
        const endingTitle = document.getElementById('ending-title');
        const endingContent = document.getElementById('ending-content');
        
        if (endingScreen) {
            endingScreen.setAttribute('role', 'main');
            if (endingTitle) {
                endingScreen.setAttribute('aria-labelledby', 'ending-title');
            }
        }
        
        if (endingContent) {
            endingContent.setAttribute('role', 'article');
            endingContent.setAttribute('aria-live', 'polite');
        }
        
        console.log('♿ [无障碍] 结局界面已增强');
    }
    
    // ========================================
    // 13. 跳转到主内容
    // ========================================
    
    function addSkipLink() {
        // 创建跳转到主菜单的链接
        const skipLink = document.createElement('a');
        skipLink.href = '#menu-screen';
        skipLink.textContent = '跳转到主菜单';
        skipLink.className = 'sr-only';
        skipLink.style.cssText = `
            position: absolute;
            top: -40px;
            left: 0;
            background: #1ABC9C;
            color: white;
            padding: 8px 16px;
            z-index: 10000;
            text-decoration: none;
            border-radius: 0 0 8px 0;
        `;
        
        skipLink.addEventListener('focus', () => {
            skipLink.style.top = '0';
        });
        
        skipLink.addEventListener('blur', () => {
            skipLink.style.top = '-40px';
        });
        
        document.body.insertBefore(skipLink, document.body.firstChild);
        
        console.log('♿ [无障碍] 跳转链接已添加');
    }
    
    // ========================================
    // 14. 屏幕阅读器公告
    // ========================================
    
    function announceToScreenReader(message, priority = 'polite') {
        const announcement = document.createElement('div');
        announcement.setAttribute('role', 'status');
        announcement.setAttribute('aria-live', priority);
        announcement.setAttribute('aria-atomic', 'true');
        announcement.className = 'sr-only';
        announcement.style.cssText = `
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        `;
        
        document.body.appendChild(announcement);
        
        // 延迟设置内容以确保屏幕阅读器能检测到变化
        setTimeout(() => {
            announcement.textContent = message;
        }, 100);
        
        // 清理
        setTimeout(() => {
            document.body.removeChild(announcement);
        }, 1000);
    }
    
    // 暴露到全局
    window.announceToScreenReader = announceToScreenReader;
    
    // ========================================
    // 初始化
    // ========================================
    
    function init() {
        // 等待 DOM 加载完成
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', runEnhancements);
        } else {
            runEnhancements();
        }
    }
    
    function runEnhancements() {
        console.log('♿ [无障碍] 开始增强...');
        
        // 添加跳转链接
        addSkipLink();
        
        // 初始化各项增强
        initKeyboardNavigation();
        enhanceLoadingScreen();
        enhanceOptionCards();
        enhanceDifficultyCards();
        enhanceToneCards();
        enhanceAttributeButtons();
        enhanceCharacterPanel();
        enhanceModal();
        enhanceSettingTabs();
        enhanceProgressBar();
        enhanceImageLoading();
        enhanceEndingScreen();
        
        // 设置 MutationObserver 监听动态添加的元素
        observeDynamicElements();
        
        console.log('♿ [无障碍] 增强完成!');
    }
    
    // ========================================
    // 监听动态元素
    // ========================================
    
    function observeDynamicElements() {
        const observer = new MutationObserver(mutations => {
            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === 1) { // 元素节点
                        // 检查是否是需要增强的元素
                        if (node.classList && node.classList.contains('option-card')) {
                            enhanceOptionCards();
                        }
                    }
                });
            });
        });
        
        observer.observe(document.body, { 
            childList: true, 
            subtree: true 
        });
    }
    
    // 启动
    init();
    
})();
