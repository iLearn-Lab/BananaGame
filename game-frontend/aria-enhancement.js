/**
 * ARIA 无障碍增强脚本
 * 为游戏界面添加无障碍标签和键盘导航支持
 * 文档：https://前端技术实现路径.md - 第14章 无障碍设计
 */

(function() {
    'use strict';

    // 检测用户偏好
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // ========================================
    // 1. 增强主菜单按钮
    // ========================================
    function enhanceMenuButtons() {
        const menuButtons = [
            { id: 'start-btn', label: '开始新游戏', shortcut: 'N' },
            { id: 'load-btn', label: '加载游戏存档', shortcut: 'L' },
            { id: 'save-manage-btn', label: '管理游戏存档', shortcut: 'S' },
            { id: 'exit-btn', label: '退出游戏', shortcut: 'Q' }
        ];

        menuButtons.forEach(btn => {
            const element = document.getElementById(btn.id);
            if (element) {
                element.setAttribute('role', 'button');
                element.setAttribute('aria-label', btn.label);
                element.setAttribute('tabindex', '0');
                // 添加快捷键提示
                element.setAttribute('accesskey', btn.shortcut);
            }
        });
    }

    // ========================================
    // 2. 增强属性选择按钮
    // ========================================
    function enhanceAttrButtons() {
        const attrContainers = document.querySelectorAll('.attr-options');
        
        attrContainers.forEach(container => {
            const attrName = container.getAttribute('data-attr') || '属性';
            
            container.setAttribute('role', 'radiogroup');
            container.setAttribute('aria-label', `${attrName}属性选择`);
            
            const buttons = container.querySelectorAll('.attr-option-btn');
            buttons.forEach((btn, index) => {
                btn.setAttribute('role', 'radio');
                btn.setAttribute('aria-checked', index === 2 ? 'true' : 'false'); // 默认"普通"选中
                btn.setAttribute('tabindex', index === 2 ? '0' : '-1');
            });
        });
    }

    // ========================================
    // 3. 增强难度选择卡片
    // ========================================
    function enhanceDifficultyCards() {
        const cards = document.querySelectorAll('.difficulty-card');
        
        cards.forEach((card, index) => {
            const difficulty = card.getAttribute('data-difficulty') || '中等';
            card.setAttribute('role', 'radio');
            card.setAttribute('aria-checked', index === 1 ? 'true' : 'false'); // 默认"中等"选中
            card.setAttribute('tabindex', index === 1 ? '0' : '-1');
            card.setAttribute('aria-label', `难度选择：${difficulty}`);
        });
    }

    // ========================================
    // 4. 增强基调选择卡片
    // ========================================
    function enhanceToneCards() {
        const cards = document.querySelectorAll('.tone-card');
        
        cards.forEach((card, index) => {
            const tone = card.getAttribute('data-tone') || '普通结局';
            card.setAttribute('role', 'radio');
            card.setAttribute('aria-checked', index === 0 ? 'true' : 'false');
            card.setAttribute('tabindex', index === 0 ? '0' : '-1');
            card.setAttribute('aria-label', `故事基调：${tone}`);
        });
    }

    // ========================================
    // 5. 增强游戏选项
    // ========================================
    function enhanceGameOptions() {
        const optionsList = document.querySelector('.options-list');
        if (optionsList) {
            optionsList.setAttribute('role', 'listbox');
            optionsList.setAttribute('aria-label', '剧情选项');
        }
        
        const options = document.querySelectorAll('.option-card');
        options.forEach((opt, index) => {
            opt.setAttribute('role', 'option');
            opt.setAttribute('tabindex', '0');
            opt.setAttribute('aria-label', `选项${index + 1}：${opt.textContent.trim()}`);
        });
    }

    // ========================================
    // 6. 增强角色面板
    // ========================================
    function enhanceCharacterPanel() {
        const panel = document.getElementById('character-panel');
        if (panel) {
            panel.setAttribute('role', 'complementary');
            panel.setAttribute('aria-label', '角色状态面板');
        }
    }

    // ========================================
    // 7. 增强加载状态提示
    // ========================================
    function enhanceLoadingStatus() {
        const loadingScreen = document.getElementById('loading-screen');
        if (loadingScreen) {
            loadingScreen.setAttribute('role', 'status');
            loadingScreen.setAttribute('aria-live', 'polite');
            loadingScreen.setAttribute('aria-label', '游戏加载中');
        }
    }

    // ========================================
    // 8. 增强表单输入
    // ========================================
    function enhanceFormInputs() {
        const themeInput = document.getElementById('theme-input');
        if (themeInput) {
            themeInput.setAttribute('aria-label', '输入游戏主题或故事背景');
            themeInput.setAttribute('aria-describedby', 'theme-hint');
        }
        
        const themeHint = document.createElement('span');
        themeHint.id = 'theme-hint';
        themeHint.className = 'sr-only';
        themeHint.textContent = '输入你想要的故事主题或背景设定，系统将根据你的输入生成独特的剧情';
        themeInput.parentNode.appendChild(themeHint);
    }

    // ========================================
    // 9. 增强存档卡片
    // ========================================
    function enhanceSaveCards() {
        const saveCards = document.querySelectorAll('.save-card');
        saveCards.forEach((card, index) => {
            card.setAttribute('role', 'article');
            card.setAttribute('aria-label', `存档${index + 1}`);
            card.setAttribute('tabindex', '0');
        });
    }

    // ========================================
    // 10. 增强结局界面
    // ========================================
    function enhanceEndingScreen() {
        const endingTitle = document.getElementById('ending-title');
        if (endingTitle) {
            endingTitle.setAttribute('role', 'heading');
            endingTitle.setAttribute('aria-level', '1');
        }
        
        const endingContent = document.getElementById('ending-content');
        if (endingContent) {
            endingContent.setAttribute('role', 'article');
            endingContent.setAttribute('aria-label', '结局内容');
        }
    }

    // ========================================
    // 11. 键盘导航支持
    // ========================================
    function setupKeyboardNavigation() {
        // 属性选项键盘导航
        document.querySelectorAll('.attr-options').forEach(container => {
            const buttons = Array.from(container.querySelectorAll('.attr-option-btn'));
            
            container.addEventListener('keydown', (e) => {
                const currentIndex = buttons.findIndex(b => b.getAttribute('aria-checked') === 'true');
                
                switch(e.key) {
                    case 'ArrowRight':
                    case 'ArrowDown':
                        e.preventDefault();
                        navigateOption(buttons, currentIndex, 1);
                        break;
                    case 'ArrowLeft':
                    case 'ArrowUp':
                        e.preventDefault();
                        navigateOption(buttons, currentIndex, -1);
                        break;
                    case ' ':
                    case 'Enter':
                        e.preventDefault();
                        selectOption(buttons, currentIndex);
                        break;
                }
            });
        });

        // 难度卡片键盘导航
        document.querySelectorAll('.difficulty-cards').forEach(container => {
            const cards = Array.from(container.querySelectorAll('.difficulty-card'));
            
            container.addEventListener('keydown', (e) => {
                const currentIndex = cards.findIndex(c => c.getAttribute('aria-checked') === 'true');
                
                switch(e.key) {
                    case 'ArrowRight':
                    case 'ArrowDown':
                        e.preventDefault();
                        navigateOption(cards, currentIndex, 1);
                        break;
                    case 'ArrowLeft':
                    case 'ArrowUp':
                        e.preventDefault();
                        navigateOption(cards, currentIndex, -1);
                        break;
                    case ' ':
                    case 'Enter':
                        e.preventDefault();
                        selectOption(cards, currentIndex);
                        break;
                }
            });
        });
    }

    function navigateOption(elements, currentIndex, direction) {
        const newIndex = Math.max(0, Math.min(elements.length - 1, currentIndex + direction));
        if (newIndex !== currentIndex) {
            elements[currentIndex].setAttribute('aria-checked', 'false');
            elements[currentIndex].setAttribute('tabindex', '-1');
            elements[newIndex].setAttribute('aria-checked', 'true');
            elements[newIndex].setAttribute('tabindex', '0');
            elements[newIndex].focus();
            elements[newIndex].click();
        }
    }

    function selectOption(elements, index) {
        elements[index].click();
    }

    // ========================================
    // 12. 屏幕阅读器提示
    // ========================================
    function addScreenReaderAnnouncements() {
        const announcer = document.createElement('div');
        announcer.id = 'sr-announcer';
        announcer.setAttribute('role', 'status');
        announcer.setAttribute('aria-live', 'polite');
        announcer.setAttribute('aria-atomic', 'true');
        announcer.className = 'sr-only';
        announcer.style.cssText = 'position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0';
        document.body.appendChild(announcer);
        
        window.announceToScreenReader = function(message) {
            announcer.textContent = '';
            setTimeout(() => {
                announcer.textContent = message;
            }, 100);
        };
    }

    // ========================================
    // 13. 隐藏装饰性元素的辅助功能
    // ========================================
    function markDecorativeElements() {
        const decorativeElements = [
            '.game-logo',
            '.version',
            '.decorative-bg',
            '.decoration'
        ];
        
        decorativeElements.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => {
                el.setAttribute('aria-hidden', 'true');
            });
        });
    }

    // ========================================
    // 初始化
    // ========================================
    function init() {
        console.log('ARIA 增强初始化...');
        
        enhanceMenuButtons();
        enhanceAttrButtons();
        enhanceDifficultyCards();
        enhanceToneCards();
        enhanceGameOptions();
        enhanceCharacterPanel();
        enhanceLoadingStatus();
        enhanceFormInputs();
        enhanceSaveCards();
        enhanceEndingScreen();
        setupKeyboardNavigation();
        addScreenReaderAnnouncements();
        markDecorativeElements();
        
        console.log('ARIA 增强完成');
    }

    // DOM 加载完成后执行
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 屏幕切换时重新增强
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'childList') {
                // 屏幕切换后重新增强选项
                setTimeout(() => {
                    enhanceGameOptions();
                    enhanceSaveCards();
                }, 100);
            }
        });
    });

    observer.observe(document.body, { childList: true, subtree: true });

})();
