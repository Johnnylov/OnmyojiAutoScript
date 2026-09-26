import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/modules/home/controllers/dashboard_controller.dart';
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/utils/platform_utils.dart';
import 'package:window_manager/window_manager.dart';

import 'solana_api.dart';
import 'solana_controller.dart';
import 'solana_tasks.dart';
import 'solana_settings.dart';
import 'solana_terminal.dart';
import 'solana_recovery.dart';
import 'solana_execution.dart';
import 'solana_overview_metrics.dart';
import 'solana_reference_icons.dart';
import 'solana_connection_settings.dart';
import 'solana_deployment.dart';
import 'solana_profile_manager.dart';
import 'solana_widgets.dart';

part 'solana_panels.dart';

/// README workbench layout. Observations are rendered only when supplied by
/// the backend; empty resource and preview slots remain explicitly unknown.
class SolanaShell extends StatefulWidget {
  final SolanaController? controller;
  final bool autoStart;
  final bool skipStartupActions;
  final bool showCaption;
  final Widget Function(String)? terminalBuilder;
  const SolanaShell({
    super.key,
    this.controller,
    this.autoStart = true,
    this.skipStartupActions = false,
    this.showCaption = true,
    this.terminalBuilder,
  });
  @override
  State<SolanaShell> createState() => _SolanaShellState();
}

class _SolanaShellState extends State<SolanaShell> {
  late final SolanaController c;
  int _page = 0;
  String? _selectedTask;
  bool _dark = false;
  bool _lowEffects = false;
  bool _treeOpen = false;
  bool _copyingTask = false;
  bool _startingExecution = false;
  String _treeSearch = '';
  final _tasksKey = GlobalKey<SolanaTasksState>();
  final _expandedGroups = <String>{};
  Map<String, List<String>> _menu = {};
  int _menuGeneration = 0;
  Timer? _previewTimer;
  static const _pageNames = [
    '总览',
    '任务配置',
    '调度中心',
    '运行统计',
    '操作审计',
    '应用设置',
    '后端部署',
  ];
  static const _purple = Color(0xFF82729E);
  static const _ink = Color(0xFF51495C);

  @override
  void initState() {
    super.initState();
    c = widget.controller ?? SolanaController();
    if (widget.autoStart) {
      _dark = GetStorage().read<bool>('solana_dark') ?? false;
      _lowEffects = GetStorage().read<bool>('solana_low_effects') ?? false;
      c.start().then((_) {
        if (mounted) {
          _loadMenu();
          c.refreshPreview();
        }
      });
      _previewTimer = Timer.periodic(const Duration(seconds: 2), (_) {
        if (_page == 0 && c.connected) c.refreshPreview();
      });
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted &&
            widget.autoStart &&
            !widget.skipStartupActions &&
            Get.isRegistered<HomeDashboardController>() &&
            Get.isRegistered<SettingsController>() &&
            Get.isRegistered<ScriptService>()) {
          unawaited(_restoreStartupPreferences());
        }
      });
    } else {
      _loadMenu();
    }
  }

  Future<void> _restoreStartupPreferences() async {
    final backend = c.backendGeneration;
    try {
      // The shared service preserves only the user's previously saved startup
      // choices. This path is never entered by read-only previews/tests.
      await Get.find<HomeDashboardController>().checkStartupConnection();
      if (!mounted || backend != c.backendGeneration) return;
      await c.start();
      if (!mounted || backend != c.backendGeneration) return;
      await _loadMenu();
      await c.refreshPreview();
    } catch (_) {
      if (!mounted || backend != c.backendGeneration) return;
      setState(() {
        c.operationFailed = true;
        c.operationMessage = '启动设置未能完成，请检查后端连接与部署日志。';
      });
    }
  }

  Future<void> _loadMenu() async {
    final generation = ++_menuGeneration;
    final backend = c.backendGeneration;
    try {
      final response = await c.api.get('/script_menu');
      if (!mounted ||
          generation != _menuGeneration ||
          backend != c.backendGeneration) {
        return;
      }
      setState(() {
        _menu = response.map(
          (key, value) => MapEntry(
            key,
            value is List
                ? value.map((item) => item.toString()).toList()
                : <String>[],
          ),
        );
        final groups = _menu.entries.where(
          (entry) =>
              !['Overview', 'Home', 'Updater', 'Tool'].contains(entry.key) &&
              entry.value.isNotEmpty,
        );
        if (groups.isNotEmpty) _expandedGroups.add(groups.first.key);
      });
    } catch (_) {
      if (mounted &&
          generation == _menuGeneration &&
          backend == c.backendGeneration) {
        setState(() => _menu = {});
      }
    }
  }

  @override
  void dispose() {
    _previewTimer?.cancel();
    if (widget.controller == null) c.dispose();
    super.dispose();
  }

  Future<void> _navigate(int value) async {
    if (_page == 1 &&
        !await (_tasksKey.currentState?.canLeave() ?? Future.value(true))) {
      return;
    }
    if (mounted) {
      setState(() {
        _page = value;
        _treeOpen = false;
      });
    }
  }

  Future<void> _openTask(String task) async {
    if (_page == 1 &&
        !await (_tasksKey.currentState?.canLeave() ?? Future.value(true))) {
      return;
    }
    if (mounted) {
      setState(() {
        _selectedTask = task;
        _page = 1;
        _treeOpen = false;
      });
    }
  }

  Future<void> _selectProfile(String value) async {
    if (_page == 1 &&
        !await (_tasksKey.currentState?.canLeave() ?? Future.value(true))) {
      return;
    }
    await c.selectProfile(value);
    if (widget.autoStart) await c.refreshPreview();
  }

  Future<void> _openConnectionSettings() async {
    if (!await (_tasksKey.currentState?.canLeave() ?? Future.value(true)) ||
        !mounted) {
      return;
    }
    if (await showSolanaConnectionSettings(context, c) && mounted) {
      setState(() {
        _selectedTask = null;
        _menu = {};
        _expandedGroups.clear();
        if (_page == 1) _page = 5;
      });
      await _loadMenu();
    }
  }

  Future<void> _openProfileManager() async {
    if (!await (_tasksKey.currentState?.canLeave() ?? Future.value(true)) ||
        !mounted) {
      return;
    }
    await showSolanaProfileManager(context, c);
    if (!mounted) return;
    await _tasksKey.currentState?.reloadFromServer();
    await _loadMenu();
  }

  Future<void> _configureLinker() async {
    if (c.busy) return;
    var enabled = true;
    final selected = c.linker.selectedNames.toSet();
    final source = textValue(c.profile['name'], '');
    final result = await showDialog<({bool enabled, Set<String> names})>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, change) => AlertDialog(
          title: const Text('连接器'),
          content: SizedBox(
            width: 360,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  SwitchListTile.adaptive(
                    contentPadding: EdgeInsets.zero,
                    title: const Text(
                      '启用多配置联动',
                      style: TextStyle(fontSize: 13),
                    ),
                    value: enabled,
                    onChanged: (value) => change(() => enabled = value),
                  ),
                  const Text(
                    '只有当前配置也被勾选时，启停和参数修改才会联动到已选配置。',
                    style: TextStyle(fontSize: 11),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    '已选 ${selected.length} 个配置',
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  for (final profile in c.profiles)
                    CheckboxListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      controlAffinity: ListTileControlAffinity.leading,
                      value: selected.contains(profile['name']),
                      title: Text(
                        textValue(profile['name']),
                        style: const TextStyle(fontSize: 12),
                      ),
                      subtitle: profile['name'] == source
                          ? const Text('当前配置', style: TextStyle(fontSize: 10))
                          : null,
                      onChanged: enabled
                          ? (value) => change(() {
                              if (value == true) {
                                selected.add(textValue(profile['name']));
                              } else {
                                selected.remove(profile['name']);
                              }
                            })
                          : null,
                    ),
                  if (c.profiles.isEmpty)
                    const Text('连接后才能选择实际配置', style: TextStyle(fontSize: 11)),
                  const SizedBox(height: 8),
                  Text(
                    enabled && selected.contains(source)
                        ? '应用后，从 $source 操作将联动 ${selected.length} 个已选配置。'
                        : '当前配置未参与联动，操作仅作用于自身。',
                    style: const TextStyle(
                      fontSize: 11,
                      color: Color(0xFF82729E),
                    ),
                  ),
                  if (c.controlReviewRequired)
                    TextButton.icon(
                      onPressed: c.reloadLinkedControl,
                      icon: const Icon(Icons.refresh, size: 16),
                      label: const Text('重新加载联动状态'),
                    ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, (
                enabled: enabled,
                names: Set<String>.from(selected),
              )),
              child: const Text('应用联动范围'),
            ),
          ],
        ),
      ),
    );
    if (result == null || !mounted || c.busy) return;
    if (!await (_tasksKey.currentState?.canLeave() ?? Future.value(true)) ||
        !mounted) {
      return;
    }
    c.linker.setEnabled(result.enabled);
    if (result.enabled) c.linker.setSelection(result.names);
    await _tasksKey.currentState?.reloadFromServer();
  }

  String? get _copyTaskId {
    final value = _page == 1
        ? _selectedTask
        : _page == 0
        ? _run['task_id']?.toString()
        : null;
    return value == null || value.isEmpty ? null : value;
  }

  Future<void> _copyTaskJson() async {
    final task = _copyTaskId;
    final config = textValue(c.profile['name'], '');
    if (task == null || config.isEmpty || _copyingTask) return;
    setState(() => _copyingTask = true);
    try {
      final payload = await c.api.get(
        '/config/task/copy-json',
        query: {'config_name': config, 'task_name': task},
      );
      await Clipboard.setData(
        ClipboardData(
          text: const JsonEncoder.withIndent('  ').convert(payload),
        ),
      );
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('已复制 $config / ${taskLabel(task)} 的非脱敏信息')),
        );
      }
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('复制失败，请检查后端连接和任务是否存在')));
      }
    } finally {
      if (mounted) setState(() => _copyingTask = false);
    }
  }

  JsonObject get _run =>
      selectedOverviewRun(c.overview.data, c.selectedProfile);

  String get _state => textValue(c.profile['state'], 'unknown');
  bool get _needsReview =>
      _run['state'] == 'needs_reconciliation' ||
      c.overview.data?['dispatch_blocked'] == true ||
      c.storage.data?['needs_reconciliation'] == true ||
      objects(c.recovery.data?['items']).isNotEmpty ||
      objects(c.recoveryOperations.data?['items']).isNotEmpty;
  SolanaExecutionState get _execution => SolanaExecutionState(
    c.profile,
    hasUncertainRun: const {
      'needs_reconciliation',
      'recovery_requested',
    }.contains(_run['state']),
    supportsRestart:
        (c.capabilities.data?['control_actions'] as List?)?.contains(
          'restart',
        ) ==
        true,
  );
  bool get _processActive => _execution.active;
  String get _taskName => taskLabel(
    textValue(_run['task_name'] ?? _run['task_id'] ?? _run['task'], '暂无运行任务'),
  );

  @override
  Widget build(BuildContext context) => Theme(
    data: solanaTheme(Brightness.light, nightBackdrop: _dark),
    child: Builder(
      builder: (context) => AnimatedBuilder(
        animation: c,
        builder: (context, _) => Scaffold(
          backgroundColor: Colors.transparent,
          body: Stack(
            children: [
              Positioned.fill(
                child: CustomPaint(
                  painter: _ReferenceBackground(
                    dark: _dark,
                    simple: _lowEffects,
                  ),
                ),
              ),
              Column(
                children: [
                  _caption(context),
                  Expanded(
                    child: LayoutBuilder(
                      builder: (context, bounds) {
                        final narrow = bounds.maxWidth < 800;
                        final treeWidth = bounds.maxWidth < 1050
                            ? 154.0
                            : 170.0;
                        final schedulerWidth = bounds.maxWidth < 1050
                            ? 238.0
                            : 264.0;
                        return Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            _rail(context, narrow),
                            Expanded(
                              child: Padding(
                                padding: const EdgeInsets.fromLTRB(
                                  0,
                                  11,
                                  9,
                                  17,
                                ),
                                child: Row(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  children: [
                                    if (!narrow) ...[
                                      SizedBox(
                                        width: treeWidth,
                                        child: _tree(context),
                                      ),
                                      const SizedBox(width: 9),
                                    ],
                                    if (!narrow) ...[
                                      SizedBox(
                                        width: schedulerWidth,
                                        child: _scheduleColumn(context),
                                      ),
                                      const SizedBox(width: 9),
                                    ],
                                    Expanded(child: _mainColumn(context)),
                                  ],
                                ),
                              ),
                            ),
                            if (narrow && _treeOpen) const SizedBox.shrink(),
                          ],
                        );
                      },
                    ),
                  ),
                ],
              ),
              if (_treeOpen)
                Positioned(
                  left: 54,
                  top: 47,
                  bottom: 17,
                  width: 220,
                  child: Material(
                    elevation: 10,
                    color: Colors.transparent,
                    borderRadius: BorderRadius.circular(12),
                    child: _tree(context),
                  ),
                ),
            ],
          ),
        ),
      ),
    ),
  );

  Widget _caption(BuildContext context) {
    final breadcrumb = Row(
      children: [
        const SizedBox(width: 4),
        const Text(
          'OASX',
          style: TextStyle(fontSize: 17, color: Color(0xFF25232D)),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 7),
          child: Text('/', style: TextStyle(fontSize: 17)),
        ),
        Text(
          textValue(c.profile['name'], '未连接').toUpperCase(),
          style: const TextStyle(fontSize: 17, color: Color(0xFF25232D)),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 7),
          child: Text('/', style: TextStyle(fontSize: 17)),
        ),
        Flexible(
          child: Text(
            _pageNames[_page],
            style: const TextStyle(
              fontSize: 17,
              fontWeight: FontWeight.w700,
              color: Color(0xFF25232D),
            ),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
    return SizedBox(
      height: 36,
      child: Row(
        children: [
          SizedBox(
            width: 64,
            child: Center(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.asset(
                  'assets/images/Icon-app.png',
                  width: 28,
                  height: 28,
                ),
              ),
            ),
          ),
          Expanded(
            child: widget.showCaption && PlatformUtils.isDesktop
                ? DragToMoveArea(child: breadcrumb)
                : breadcrumb,
          ),
          if (!c.connected && c.overview.data != null)
            const Padding(
              padding: EdgeInsets.only(right: 10),
              child: Text(
                '离线 · 状态未更新',
                style: TextStyle(fontSize: 10, color: solanaAmber),
              ),
            ),
          _captionButton(
            Icons.remove,
            '最小化',
            widget.showCaption ? () => windowManager.minimize() : null,
          ),
          _captionButton(
            Icons.crop_square,
            '最大化',
            widget.showCaption
                ? () async {
                    if (await windowManager.isMaximized()) {
                      await windowManager.unmaximize();
                    } else {
                      await windowManager.maximize();
                    }
                  }
                : null,
          ),
          _captionButton(
            Icons.close,
            '关闭',
            widget.showCaption ? () => windowManager.close() : null,
          ),
        ],
      ),
    );
  }

  Widget _captionButton(
    IconData icon,
    String tooltip,
    VoidCallback? onPressed,
  ) => SizedBox(
    width: 43,
    height: 32,
    child: IconButton(
      tooltip: tooltip,
      padding: EdgeInsets.zero,
      onPressed: onPressed,
      icon: Icon(icon, size: 14, color: const Color(0xFF514E5B)),
    ),
  );

  Widget _rail(BuildContext context, bool narrow) => SizedBox(
    width: 64,
    child: Column(
      children: [
        const SizedBox(height: 18),
        _railButton(Icons.home_outlined, '主页', _page == 0, () => _navigate(0)),
        const SizedBox(height: 10),
        Expanded(
          child: ListView(
            padding: EdgeInsets.zero,
            children: [
              for (final profile in c.profiles)
                Padding(
                  padding: const EdgeInsets.only(bottom: 9),
                  child: _railButton(
                    profile['state'] == 'running'
                        ? Icons.pause_circle_filled
                        : Icons.play_circle_fill,
                    textValue(profile['name']).toUpperCase(),
                    c.selectedProfile == profile['id'],
                    () => _selectProfile(textValue(profile['id'])),
                  ),
                ),
              if (c.profiles.isEmpty)
                _railButton(
                  Icons.link_off_rounded,
                  '未连接',
                  false,
                  () => _navigate(5),
                ),
              if (narrow)
                _railButton(
                  Icons.menu_rounded,
                  '任务',
                  _treeOpen,
                  () => setState(() => _treeOpen = !_treeOpen),
                ),
            ],
          ),
        ),
        IconButton(
          tooltip: '应用设置',
          onPressed: () => _navigate(5),
          icon: const Icon(Icons.settings, size: 25, color: _purple),
        ),
        const SizedBox(height: 7),
      ],
    ),
  );

  Widget _railButton(
    IconData icon,
    String name,
    bool selected,
    VoidCallback onTap,
  ) => Tooltip(
    message: name,
    child: InkWell(
      onTap: onTap,
      child: Column(
        children: [
          Container(
            width: 50,
            height: 29,
            decoration: BoxDecoration(
              color: selected
                  ? _purple.withValues(alpha: .43)
                  : Colors.transparent,
              borderRadius: BorderRadius.circular(18),
              boxShadow: selected
                  ? [
                      BoxShadow(
                        color: _purple.withValues(alpha: .15),
                        blurRadius: 9,
                      ),
                    ]
                  : null,
            ),
            child: Icon(
              icon,
              size: 27,
              color: selected ? Colors.white : const Color(0xFF4B425C),
            ),
          ),
          const SizedBox(height: 2),
          Text(
            name,
            style: const TextStyle(fontSize: 10, color: _purple),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    ),
  );

  Widget _tree(BuildContext context) {
    final device = object(c.profile['device']);
    return Surface(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            height: 48,
            padding: const EdgeInsets.symmetric(horizontal: 10),
            decoration: BoxDecoration(
              color: const Color(0xFFB6E8F5).withValues(alpha: .25),
              border: Border(
                bottom: BorderSide(color: Colors.white.withValues(alpha: .55)),
              ),
            ),
            child: Row(
              children: [
                Container(
                  width: 27,
                  height: 27,
                  decoration: BoxDecoration(
                    color: const Color(0xFF318DEC),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: const Icon(
                    Icons.phone_android_rounded,
                    size: 22,
                    color: Colors.white,
                  ),
                ),
                const SizedBox(width: 7),
                Expanded(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        textValue(device['name'], '设备等待连接'),
                        style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          color: _purple,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        textValue(device['serial'], '地址未确认'),
                        style: const TextStyle(
                          fontSize: 9,
                          color: Color(0xFF8C8598),
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 5),
              children: [
                _treeItem(
                  Icons.article_outlined,
                  '总览',
                  () => _navigate(0),
                  selected: _page == 0,
                ),
                for (final group in _menu.entries.where(
                  (entry) => ![
                    'Overview',
                    'Home',
                    'Updater',
                    'Tool',
                  ].contains(entry.key),
                )) ...[
                  if (_treeSearch.isEmpty ||
                      taskLabel(
                        group.key,
                      ).toLowerCase().contains(_treeSearch) ||
                      group.value.any(
                        (task) =>
                            taskLabel(task).toLowerCase().contains(_treeSearch),
                      ))
                    _treeItem(
                      group.value.isEmpty
                          ? _taskTreeIcon(group.key)
                          : _expandedGroups.contains(group.key)
                          ? Icons.arrow_drop_down
                          : Icons.arrow_right,
                      taskLabel(group.key),
                      () {
                        if (group.value.isEmpty) {
                          _openTask(group.key);
                        } else {
                          setState(() {
                            if (!_expandedGroups.add(group.key)) {
                              _expandedGroups.remove(group.key);
                            }
                          });
                        }
                      },
                      taskId: group.value.isEmpty ? group.key : null,
                      selected:
                          group.value.isEmpty &&
                          _page == 1 &&
                          _selectedTask == group.key,
                    ),
                  if (_expandedGroups.contains(group.key) ||
                      _treeSearch.isNotEmpty)
                    for (final task in group.value.where(
                      (task) =>
                          _treeSearch.isEmpty ||
                          taskLabel(task).toLowerCase().contains(_treeSearch),
                    ))
                      _treeItem(
                        _taskTreeIcon(task),
                        taskLabel(task),
                        () => _openTask(task),
                        selected: _page == 1 && _selectedTask == task,
                        taskId: task,
                        indent: 16,
                      ),
                ],
                if (_menu.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(12),
                    child: Text(
                      c.connected ? '暂无任务目录' : '连接后加载任务目录',
                      style: const TextStyle(fontSize: 10, color: _purple),
                    ),
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(9, 5, 8, 9),
            child: Row(
              children: [
                Expanded(
                  child: SizedBox(
                    height: 30,
                    child: TextField(
                      style: const TextStyle(fontSize: 10),
                      decoration: const InputDecoration(
                        prefixIcon: Icon(Icons.search, size: 13),
                        prefixIconConstraints: BoxConstraints(minWidth: 24),
                        hintText: '搜索实例/分组',
                        hintStyle: TextStyle(fontSize: 10),
                        contentPadding: EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 8,
                        ),
                      ),
                      onChanged: (value) =>
                          setState(() => _treeSearch = value.toLowerCase()),
                    ),
                  ),
                ),
                const SizedBox(width: 4),
                _toolIcon(
                  Icons.link_rounded,
                  c.linker.enabled ? '关闭连接器' : '开启连接器',
                  c.connected && !c.busy ? _configureLinker : null,
                  selected: c.linker.enabled,
                  size: 15,
                  width: 27,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _treeItem(
    IconData icon,
    String text,
    VoidCallback onTap, {
    bool selected = false,
    double indent = 0,
    String? taskId,
  }) {
    final running =
        taskId != null &&
        _run['state'] == 'running' &&
        taskId.replaceAll('_', '').toLowerCase() ==
            textValue(_run['task_id']).replaceAll('_', '').toLowerCase();
    final iconColor = running
        ? const Color(0xFF8080DA)
        : selected
        ? const Color(0xFF6B588A)
        : taskId != null && _treeTaskType(taskId) == 'restart'
        ? const Color(0xFF9390EA)
        : taskId != null
        ? const Color(0xFF51495F)
        : _purple;
    return SizedBox(
      height: 30,
      child: Material(
        color: selected && taskId != null
            ? _purple.withValues(alpha: .09)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: EdgeInsets.only(left: 3 + indent, right: 3),
            child: Row(
              children: [
                if (taskId != null)
                  SolanaReferenceIcon(
                    _referenceTaskIconName(taskId),
                    key: ValueKey('task-icon-$taskId'),
                    size: 16,
                    color: selected || running ? iconColor : null,
                  )
                else
                  Icon(
                    icon,
                    key: taskId == null ? null : ValueKey('task-icon-$taskId'),
                    size: 14,
                    color: iconColor,
                  ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    text,
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: selected
                          ? FontWeight.w600
                          : FontWeight.normal,
                      color: running
                          ? const Color(0xFF8080DA)
                          : selected
                          ? const Color(0xFF6B588A)
                          : _purple,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (running)
                  const Tooltip(
                    message: '正在运行',
                    child: Icon(Icons.circle, size: 5, color: solanaGreen),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _treeTaskType(String task) =>
      task.split('@').first.replaceAll('_', '').toLowerCase();

  String _referenceTaskIconName(String task) => switch (_treeTaskType(task)) {
    'script' || 'alas' => 'tree-script',
    'restart' => 'tree-restart',
    'globalgame' || 'general' => 'tree-global',
    _ => 'tree-plan-document',
  };

  IconData _taskTreeIcon(String task) => switch (_treeTaskType(task)) {
    'script' || 'alas' => Icons.data_object,
    'restart' => Icons.refresh_rounded,
    'globalgame' || 'general' => Icons.settings_outlined,
    _ => Icons.description,
  };

  Widget _scheduleColumn(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      SizedBox(
        height: 47,
        child: Surface(
          padding: const EdgeInsets.symmetric(horizontal: 9),
          child: Row(
            children: [
              const Expanded(
                child: Text(
                  '调度器',
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: _ink,
                  ),
                ),
              ),
              if (_needsReview)
                _toolIcon(
                  Icons.warning_amber_rounded,
                  '异常恢复与状态核验',
                  () => showSolanaRecovery(context, c),
                  size: 19,
                  width: 25,
                ),
              _executionControl(context),
              const SizedBox(width: 5),
              Tooltip(
                message: c.connected ? stateLabel(_state) : '状态未知',
                child: Container(
                  width: 13,
                  height: 13,
                  decoration: BoxDecoration(
                    color: c.connected
                        ? _processActive
                              ? const Color(0xFFFA9D08)
                              : _purple
                        : Colors.grey,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
              const SizedBox(width: 3),
              _toolIcon(
                Icons.power_settings_new,
                _execution.powerExplanation,
                _powerCallback(context),
                size: 25,
                width: 32,
              ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
      SizedBox(
        height: 90 * MediaQuery.textScalerOf(context).scale(1).clamp(1.0, 1.5),
        child: Surface(
          padding: const EdgeInsets.fromLTRB(9, 8, 8, 6),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                _processActive
                    ? '运行中'
                    : _execution.retry
                    ? '运行异常'
                    : '当前任务',
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: _ink,
                ),
              ),
              const Divider(
                height: 13,
                thickness: .7,
                color: Color(0xFF998CA8),
              ),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _taskName,
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: _ink,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  if (_run.isNotEmpty)
                    TextButton(
                      onPressed: () =>
                          _openTask(textValue(_run['task_id'] ?? _run['task'])),
                      style: TextButton.styleFrom(
                        minimumSize: Size.zero,
                        padding: EdgeInsets.zero,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                      child: const Text(
                        '设置',
                        style: TextStyle(fontSize: 10, color: _purple),
                      ),
                    ),
                ],
              ),
              Text(
                _run.isEmpty
                    ? c.connected
                          ? stateLabel(_state)
                          : '状态未知'
                    : displayTime(_run['started_at'] ?? _run['updated_at']),
                style: const TextStyle(fontSize: 10, color: Color(0xFF93899E)),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
      Expanded(
        child: Surface(
          padding: const EdgeInsets.fromLTRB(9, 8, 8, 8),
          child: _queueList(context),
        ),
      ),
      const SizedBox(height: 8),
      AspectRatio(aspectRatio: 16 / 9, child: _preview()),
    ],
  );

  Widget _queueList(BuildContext context) {
    final ready = objects(c.scheduler.data?['ready']);
    final waiting = objects(c.scheduler.data?['waiting']);
    return ListView(
      padding: EdgeInsets.zero,
      children: [
        Row(
          children: [
            const Text(
              '队列中',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w600,
                color: _ink,
              ),
            ),
            if (c.scheduler.data != null && c.scheduler.error != null) ...[
              const SizedBox(width: 6),
              const Tooltip(
                message: '队列刷新失败，当前显示上次成功读取的结果。',
                child: Icon(Icons.info_outline, size: 13, color: solanaAmber),
              ),
            ],
          ],
        ),
        const Divider(height: 16, thickness: .7, color: Color(0xFF998CA8)),
        if (ready.isEmpty) _queueEmpty(_queueEmptyText('暂无待执行任务')),
        for (final task in ready) _queueEntry(task),
        const SizedBox(height: 8),
        const Text(
          '等待中',
          style: TextStyle(
            fontSize: 14,
            fontWeight: FontWeight.w600,
            color: _ink,
          ),
        ),
        const Divider(height: 16, thickness: .7, color: Color(0xFF998CA8)),
        if (waiting.isEmpty) _queueEmpty(_queueEmptyText('暂无等待任务')),
        for (final task in waiting) _queueEntry(task),
      ],
    );
  }

  String _queueEmptyText(String emptyText) {
    // An empty successful snapshot is still data. Polling/live events must not
    // replace it with loading text on every background refresh.
    if (c.scheduler.data != null) return emptyText;
    if (c.scheduler.error != null) return '队列数据暂不可用';
    return c.scheduler.loading ? '正在读取队列…' : '等待队列数据';
  }

  Widget _queueEmpty(String text) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 8),
    child: Text(
      text,
      style: const TextStyle(fontSize: 11, color: Color(0xFF968D9F)),
    ),
  );
  Widget _queueEntry(JsonObject task) => Padding(
    padding: const EdgeInsets.only(bottom: 11),
    child: Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                taskLabel(task['task_name'] ?? task['task'] ?? task['task_id']),
                style: const TextStyle(fontSize: 13, color: _ink),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              Text(
                task['next_run'] != null
                    ? displayTime(task['next_run'])
                    : reasonLabel(textValue(task['reason'], '等待确认')),
                style: const TextStyle(fontSize: 10, color: Color(0xFF968D9F)),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ],
          ),
        ),
        _toolIcon(
          Icons.play_arrow_rounded,
          '立即执行：${taskLabel(task['task'] ?? task['task_id'])}',
          c.canControl && !c.controlReviewRequired
              ? () => c.quickScheduleTask(
                  textValue(task['task'] ?? task['task_id']),
                )
              : null,
          size: 16,
          width: 24,
        ),
        const SizedBox(width: 6),
        InkWell(
          onTap: () => _openTask(textValue(task['task'] ?? task['task_id'])),
          child: const Text(
            '设置',
            style: TextStyle(fontSize: 10, color: _purple),
          ),
        ),
      ],
    ),
  );

  Widget _preview() {
    final data = c.preview.data;
    Widget? frame;
    if (data?['available'] == true && data?['image_base64'] is String) {
      try {
        frame = Image.memory(
          base64Decode(data!['image_base64']),
          fit: BoxFit.cover,
          gaplessPlayback: true,
          errorBuilder: (_, __, ___) => _previewEmpty('画面无法显示'),
        );
      } catch (_) {
        frame = null;
      }
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: DecoratedBox(
        decoration: const BoxDecoration(color: Color(0xFF273141)),
        child: Stack(
          fit: StackFit.expand,
          children: [
            frame ??
                _previewEmpty(c.preview.error == null ? '暂无设备画面' : '设备画面暂不可用'),
            if (frame != null)
              Positioned(
                right: 5,
                bottom: 5,
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 5,
                    vertical: 2,
                  ),
                  color: Colors.black45,
                  child: Text(
                    '${displayTime(data?['occurred_at'])}${data?['stale'] == true || !c.connected ? ' · 最近画面' : ''}',
                    style: const TextStyle(fontSize: 9, color: Colors.white70),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _previewEmpty(String label) => Column(
    mainAxisAlignment: MainAxisAlignment.center,
    children: [
      const Icon(
        Icons.desktop_windows_outlined,
        size: 29,
        color: Color(0xFF8793A5),
      ),
      const SizedBox(height: 8),
      Text(
        label,
        style: const TextStyle(fontSize: 11, color: Color(0xFFB2BDCE)),
      ),
      const SizedBox(height: 3),
      const Text(
        '等待执行器提供最近画面',
        style: TextStyle(fontSize: 9, color: Color(0xFF8290A6)),
      ),
    ],
  );

  Widget _mainColumn(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      SizedBox(height: 47, child: _toolbar(context)),
      const SizedBox(height: 8),
      if (!c.supported && c.capabilities.error != null) ...[
        Notice(
          c.capabilities.error!.code == 'unsupported'
              ? '当前后端尚未提供控制台接口，请连接支持新接口的后端或升级后端。'
              : c.capabilities.error!.message,
          error: true,
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: _openConnectionSettings,
            icon: const Icon(Icons.settings_ethernet_rounded, size: 16),
            label: const Text('连接设置'),
          ),
        ),
        const SizedBox(height: 8),
      ],
      if (c.operationMessage != null) ...[
        _operationFeedback(),
        const SizedBox(height: 8),
      ],
      if (c.controlReviewRequired)
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: c.busy ? null : c.reloadLinkedControl,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('重新加载联动状态'),
          ),
        ),
      Expanded(
        child: switch (_page) {
          0 => _overviewCompact(context),
          1 => SolanaTasks(
            key: _tasksKey,
            controller: c,
            showCatalog: false,
            initialTask: _selectedTask,
          ),
          2 => _scheduler(context),
          3 => _statistics(context),
          4 => _audit(context),
          6 => SolanaDeployment(onBack: () => _navigate(5)),
          _ => SolanaSettings(
            controller: c,
            dark: _dark,
            lowEffects: _lowEffects,
            onDark: (value) {
              setState(() => _dark = value);
              if (widget.autoStart) GetStorage().write('solana_dark', value);
            },
            onLowEffects: (value) {
              setState(() => _lowEffects = value);
              if (widget.autoStart) {
                GetStorage().write('solana_low_effects', value);
              }
            },
            onConnectionSettings: _openConnectionSettings,
            onDeployment: () => _navigate(6),
            onManageProfiles: _openProfileManager,
          ),
        },
      ),
    ],
  );

  Widget _operationFeedback() {
    final message = c.operationMessage!;
    if (message.length <= 160) {
      return Notice(
        message,
        error: c.operationFailed,
        onClose: c.dismissMessage,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Notice(
          c.operationFailed ? '部分操作未完成或结果待核验，请查看逐项结果。' : '操作结果已返回，请查看逐项结果。',
          error: c.operationFailed,
          onClose: c.dismissMessage,
        ),
        Align(
          alignment: Alignment.centerRight,
          child: TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (context) => AlertDialog(
                title: const Text('操作结果明细'),
                content: SizedBox(
                  width: 540,
                  height: 320,
                  child: SingleChildScrollView(
                    child: SelectableText(
                      message.replaceAll('；', '；\n'),
                      style: const TextStyle(fontSize: 12),
                    ),
                  ),
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('关闭'),
                  ),
                ],
              ),
            ),
            child: const Text('查看逐项结果'),
          ),
        ),
      ],
    );
  }

  Widget _toolbar(BuildContext context) => Surface(
    padding: const EdgeInsets.symmetric(horizontal: 7),
    child: Row(
      children: [
        if (MediaQuery.sizeOf(context).width < 800) _executionControl(context),
        _toolIcon(
          Icons.image_outlined,
          '总览',
          () => _navigate(0),
          selected: _page == 0,
          referenceIcon: 'toolbar-01',
          size: 30,
        ),
        _toolIcon(
          Icons.gesture_rounded,
          '任务中心',
          () => _navigate(1),
          selected: _page == 1,
          referenceIcon: 'toolbar-02',
          size: 30,
        ),
        _toolIcon(
          Icons.pie_chart_outline_rounded,
          '运行统计',
          () => _navigate(3),
          selected: _page == 3,
          referenceIcon: 'toolbar-03',
          size: 30,
        ),
        _toolIcon(
          Icons.my_location_rounded,
          '调度中心',
          () => _navigate(2),
          selected: _page == 2,
          referenceIcon: 'toolbar-04',
          size: 30,
        ),
        _toolIcon(
          Icons.article_outlined,
          '操作审计',
          () => _navigate(4),
          selected: _page == 4,
          referenceIcon: 'toolbar-05',
          size: 30,
        ),
        const Spacer(),
        if (MediaQuery.sizeOf(context).width < 800)
          _toolIcon(
            Icons.power_settings_new,
            _execution.powerExplanation,
            _powerCallback(context),
            size: 20,
          ),
        _toolIcon(
          Icons.flash_on,
          '立即全部执行任务',
          c.canControl && !c.controlReviewRequired
              ? () => c.bulkQuickSchedule(runNow: true)
              : null,
          size: 20,
        ),
        _toolIcon(
          Icons.copy_outlined,
          _copyTaskId == null ? '先选择任务以复制非脱敏信息' : '复制非脱敏信息',
          c.connected && _copyTaskId != null && !_copyingTask
              ? _copyTaskJson
              : null,
          size: 19,
        ),
        _toolIcon(
          Icons.stop_circle_outlined,
          '立即停止',
          c.canAttemptSafetyControl && _processActive
              ? () => _immediateStop(context)
              : null,
          size: 19,
        ),
      ],
    ),
  );

  Widget _executionControl(BuildContext context) {
    final execution = _execution;
    return Tooltip(
      message: execution.explanation,
      child: FilledButton.icon(
        key: const ValueKey('execution-primary'),
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, 30),
          padding: const EdgeInsets.symmetric(horizontal: 8),
          textStyle: solanaFormTextStyle.copyWith(fontSize: 11),
        ),
        onPressed:
            c.canControl &&
                !c.controlReviewRequired &&
                !_startingExecution &&
                execution.action != null
            ? () => _executePrimary(context, execution.action!)
            : null,
        icon: Icon(
          execution.action == 'pause'
              ? Icons.pause_rounded
              : execution.retry
              ? Icons.refresh_rounded
              : Icons.play_arrow_rounded,
          size: 16,
        ),
        label: Text(execution.label),
      ),
    );
  }

  VoidCallback? _powerCallback(BuildContext context) {
    if (_startingExecution) return null;
    final action = _execution.powerAction;
    if (action == 'safe_stop') {
      return c.canAttemptSafetyControl ? () => c.control(action) : null;
    }
    return c.canControl && !c.controlReviewRequired
        ? () => _executePrimary(context, action)
        : null;
  }

  Future<void> _executePrimary(BuildContext context, String action) async {
    if (_startingExecution) return;
    if (action != 'start' && action != 'restart') {
      await c.control(action);
      return;
    }
    setState(() => _startingExecution = true);
    try {
      if (!await (_tasksKey.currentState?.canLeave() ?? Future.value(true)) ||
          !context.mounted) {
        return;
      }
      if (action == 'restart') {
        await c.control('restart');
      } else {
        await startSolanaExecution(context, c);
      }
    } finally {
      if (mounted) setState(() => _startingExecution = false);
    }
  }

  Widget _toolIcon(
    IconData icon,
    String tooltip,
    VoidCallback? onPressed, {
    bool selected = false,
    double size = 23,
    double width = 34,
    String? referenceIcon,
  }) => SizedBox(
    width: width,
    height: 34,
    child: IconButton(
      padding: EdgeInsets.zero,
      constraints: BoxConstraints.tightFor(width: width, height: 34),
      tooltip: tooltip,
      onPressed: onPressed,
      icon: referenceIcon != null
          ? SolanaReferenceIcon(referenceIcon, size: size)
          : Icon(
              icon,
              size: size,
              color: selected
                  ? const Color(0xFF7C669E)
                  : onPressed == null
                  ? const Color(0xFFA49AAC)
                  : const Color(0xFF83788F),
            ),
    ),
  );

  Widget _overviewCompact(BuildContext context) => LayoutBuilder(
    builder: (context, bounds) {
      final scale = MediaQuery.textScalerOf(
        context,
      ).scale(1).clamp(1.0, 1.5).toDouble();
      final minimumHeight = 343 * scale + 26 + 190;
      final content = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(height: 107 * scale, child: _liveMetrics()),
          const SizedBox(height: 9),
          SizedBox(height: 96 * scale, child: _resources()),
          const SizedBox(height: 9),
          SizedBox(height: 140 * scale, child: _goalCards()),
          const SizedBox(height: 8),
          Expanded(
            child: Surface(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 8),
              child:
                  widget.terminalBuilder?.call(
                    textValue(c.profile['name'], ''),
                  ) ??
                  SolanaTerminal(
                    scriptName: textValue(c.profile['name'], ''),
                    autoConnect: widget.autoStart && c.connected,
                  ),
            ),
          ),
        ],
      );
      if (bounds.maxHeight < minimumHeight) {
        return SingleChildScrollView(
          child: SizedBox(height: minimumHeight, child: content),
        );
      }
      return content;
    },
  );

  Widget _liveMetrics() {
    final run = _run;
    final totals = object(c.statistics.data?['totals']);
    final metrics = SolanaOverviewMetrics(
      run: run,
      cycle: selectedExecutionCycle(c.overview.data, c.selectedProfile),
    );
    final elapsed = numberValue(
      run['elapsed_seconds'] ??
          run['execution_seconds'] ??
          run['duration_seconds'],
    );
    final targetSeconds = numberValue(run['target_seconds']);
    final cells = <(SolanaOverviewMetric, Color)>[
      (
        SolanaOverviewMetric(
          value: '${_clock(elapsed)} / ${_clock(targetSeconds)}',
          label: '运行时间',
          tooltip: '当前任务的实际执行时间 / 时间目标；暂停和等待不计入实际执行时间。',
          fraction:
              elapsed != null && targetSeconds != null && targetSeconds > 0
              ? (elapsed / targetSeconds).clamp(0, 1).toDouble()
              : null,
        ),
        const Color(0xFF32323A),
      ),
      (metrics.current, const Color(0xFFEF9808)),
      (metrics.battles, const Color(0xFFFF435E)),
      (
        SolanaOverviewMetric(
          value: textValue(totals['started']),
          label: '执行次数 · 所选范围',
          tooltip: '统计页所选日期范围内，当前配置已开始的任务运行次数。',
        ),
        const Color(0xFF12B8E1),
      ),
      (metrics.tasks, const Color(0xFF7916D2)),
      (
        SolanaOverviewMetric(
          value: run.isEmpty ? '暂无运行目标' : _taskName,
          label: '目标',
          tooltip: '当前任务名称。',
        ),
        const Color(0xFFECCB05),
      ),
    ];
    return Surface(
      padding: const EdgeInsets.fromLTRB(14, 6, 15, 8),
      child: Column(
        children: [
          for (var row = 0; row < 2; row++)
            Expanded(
              child: Row(
                children: [
                  for (var column = 0; column < 3; column++)
                    Expanded(
                      child: Padding(
                        padding: EdgeInsets.only(right: column < 2 ? 17 : 0),
                        child: _miniMetric(cells[row * 3 + column]),
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _miniMetric((SolanaOverviewMetric, Color) entry) {
    final (metric, color) = entry;
    return Tooltip(
      key: ValueKey('overview-metric-${metric.label}'),
      message: metric.tooltip,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 7),
            child: Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  metric.value,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: _ink,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 1),
                Text(
                  metric.subtitle,
                  style: const TextStyle(
                    fontSize: 10,
                    color: Color(0xFF887F96),
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 3),
                SizedBox(
                  height: 3,
                  child: LinearProgressIndicator(
                    value: metric.fraction ?? 0,
                    backgroundColor: _purple.withValues(alpha: .22),
                    color: const Color(0xFF9D7BC7),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _clock(num? seconds) {
    if (seconds == null) return '—';
    final value = seconds.toInt();
    return '${(value ~/ 3600).toString().padLeft(2, '0')}:${((value % 3600) ~/ 60).toString().padLeft(2, '0')}:${(value % 60).toString().padLeft(2, '0')}';
  }

  Widget _resources() {
    const slots = [
      ('assets/solana/stamina.png', '体力', 'stamina'),
      ('assets/solana/jade.png', '勾玉', 'jade'),
      ('assets/solana/coins.png', '金币', 'coins'),
      ('assets/solana/summon_tickets.png', '蓝票', 'summon_tickets'),
      ('assets/solana/gold_scales.png', '金蛇皮', 'gold_scales'),
      ('assets/solana/spirit_tickets.png', '御灵券', 'spirit_tickets'),
      ('assets/solana/raid_tickets.png', '突破券', 'raid_tickets'),
      ('assets/solana/purple_scales.png', '紫蛇皮', 'purple_scales'),
    ];
    final observations = object(c.overview.data?['resources']);
    return Surface(
      padding: const EdgeInsets.fromLTRB(14, 5, 10, 5),
      child: Column(
        children: [
          for (var row = 0; row < 2; row++)
            Expanded(
              child: Row(
                children: [
                  for (var column = 0; column < 4; column++)
                    Expanded(
                      child: Builder(
                        builder: (_) {
                          final slot = slots[row * 4 + column];
                          final value = object(observations[slot.$3]);
                          return Row(
                            crossAxisAlignment: CrossAxisAlignment.center,
                            children: [
                              Image.asset(
                                slot.$1,
                                width: 18,
                                height: 18,
                                fit: BoxFit.contain,
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Text(
                                      textValue(value['value']),
                                      style: const TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w600,
                                        color: _ink,
                                      ),
                                    ),
                                    const SizedBox(height: 1),
                                    Text(
                                      '${slot.$2} · ${value['value'] == null ? '未识别' : '已识别'}',
                                      style: const TextStyle(
                                        fontSize: 9,
                                        color: Color(0xFF887F96),
                                      ),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          );
                        },
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _goalCards() {
    final candidates = <JsonObject>[];
    final seen = <String>{};
    for (final item in [
      if (_run.isNotEmpty) _run,
      ...objects(c.scheduler.data?['ready']),
      ...objects(c.scheduler.data?['waiting']),
    ]) {
      final key = textValue(item['task_id'] ?? item['task']);
      if (seen.add(key)) candidates.add(item);
    }
    return Surface(
      padding: const EdgeInsets.fromLTRB(15, 8, 14, 8),
      child: Column(
        children: [
          for (var row = 0; row < 2; row++)
            Expanded(
              child: Row(
                children: [
                  for (var column = 0; column < 3; column++)
                    Expanded(
                      child: Padding(
                        padding: EdgeInsets.only(
                          left: column == 0 ? 0 : 10,
                          top: row == 0 ? 0 : 8,
                        ),
                        child: row * 3 + column >= candidates.length
                            ? _emptyGoal()
                            : _goal(candidates[row * 3 + column]),
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _emptyGoal() => Container(
    alignment: Alignment.centerLeft,
    decoration: BoxDecoration(
      border: Border(
        right: BorderSide(color: _purple.withValues(alpha: .14)),
        bottom: BorderSide(color: _purple.withValues(alpha: .14)),
      ),
    ),
    child: const Text(
      '暂无更多目标',
      style: TextStyle(fontSize: 10, color: Color(0xFFAAA1B3)),
    ),
  );
  Widget _goal(JsonObject task) => Container(
    padding: const EdgeInsets.only(right: 4),
    decoration: BoxDecoration(
      border: Border(
        right: BorderSide(color: _purple.withValues(alpha: .3)),
        bottom: BorderSide(color: _purple.withValues(alpha: .3)),
      ),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                taskLabel(task['task_name'] ?? task['task'] ?? task['task_id']),
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: _ink,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(
                color: task['state'] == 'running'
                    ? const Color(0xFF0E93FD)
                    : solanaAmber,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 3),
            Text(
              task['state'] == 'running'
                  ? '执行中'
                  : task['state'] == 'paused'
                  ? '已暂停'
                  : '等待中',
              style: const TextStyle(fontSize: 9, color: Color(0xFF8F829C)),
            ),
          ],
        ),
        Text(
          '进度：${task['current_count'] ?? '等待确认'}${task['remaining_target'] != null ? ' / 剩余 ${task['remaining_target']}' : ''}',
          style: const TextStyle(
            fontSize: 10,
            height: 1,
            color: Color(0xFF8F829C),
          ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        Text(
          '当前阶段：${task['state'] != null ? stateLabel(task['state']) : reasonLabel(textValue(task['reason'], '等待调度'))}',
          style: const TextStyle(
            fontSize: 10,
            height: 1,
            color: Color(0xFF8F829C),
          ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        Text(
          textValue(task['profile_name'] ?? c.profile['name']),
          style: const TextStyle(
            fontSize: 10,
            height: 1,
            color: Color(0xFF8F829C),
          ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ],
    ),
  );

  Future<void> _immediateStop(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('立即停止当前运行？'),
        content: const Text('这会强制结束执行。未完成的操作需要在下次运行前重新识别和核验。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('返回'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('立即停止'),
          ),
        ],
      ),
    );
    if (confirmed == true) await c.control('immediate_stop');
  }
}

class _ReferenceBackground extends CustomPainter {
  final bool dark;
  final bool simple;
  const _ReferenceBackground({required this.dark, required this.simple});
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawColor(
      dark ? const Color(0xFF292835) : const Color(0xFFFAFBFD),
      BlendMode.src,
    );
    if (simple) return;
    final scale = size.width / 1124;
    void blob(
      double x,
      double y,
      double w,
      double h,
      Color color, {
      double rotation = 0,
    }) {
      canvas.save();
      canvas.translate(x * scale, y * size.height / 751);
      canvas.rotate(rotation);
      canvas.drawOval(
        Rect.fromCenter(
          center: Offset.zero,
          width: w * scale,
          height: h * size.height / 751,
        ),
        Paint()
          ..color = color.withValues(alpha: dark ? .2 : 1)
          ..maskFilter = MaskFilter.blur(BlurStyle.normal, 32 * scale),
      );
      canvas.restore();
    }

    blob(354, 2, 815, 125, const Color(0xFFBCEFF2));
    blob(349, 323, 355, 720, const Color(0xFFC593A9), rotation: .14);
    blob(170, 125, 130, 210, const Color(0xFFD893A2), rotation: -.55);
    blob(83, 452, 230, 370, const Color(0xFFE9DEEF));
    blob(278, 690, 394, 185, const Color(0xFFD5F4F1));
    blob(687, 679, 392, 132, const Color(0xFFF0DAED));
    blob(967, 118, 83, 284, const Color(0xFFB68A9A), rotation: .36);
    blob(879, 484, 455, 195, const Color(0xFFD9EFF4));
  }

  @override
  bool shouldRepaint(_ReferenceBackground old) =>
      dark != old.dark || simple != old.simple;
}
