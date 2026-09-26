part of 'log_center_panel.dart';

/// Error detail page with progressive image and log rendering.
class LogCenterErrorDetailView extends StatelessWidget {
  /// Creates one error detail view.
  const LogCenterErrorDetailView({
    super.key,
    required this.controller,
    required this.detailScrollController,
    required this.detailHorizontalScrollController,
  });

  /// Log browser controller.
  final ScriptLogBrowserController controller;

  /// Vertical scroll controller for the detail log list.
  final ScrollController detailScrollController;

  /// Horizontal scroll controller for non-wrapped detail log text.
  final ScrollController detailHorizontalScrollController;

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      final detail = controller.selectedErrorDetail.value;
      if (detail == null) {
        return _buildLoading(context);
      }
      return _buildDetailScrollView(
        context,
        detail,
        title: _formatDetailTime(detail),
      );
    });
  }

  /// Builds detail header while data is still loading.
  Widget _buildLoading(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildHeader(context, _selectedErrorTitle()),
        Expanded(
          child: Center(
            child: controller.errorDetailLoading.value
                ? const CircularProgressIndicator()
                : Text(I18n.homeLogSelectError.tr),
          ),
        ),
      ],
    );
  }

  /// Builds a detail header with a back action.
  Widget _buildHeader(BuildContext context, String title, {Widget? trailing}) {
    return Row(
      children: [
        IconButton(
          tooltip: I18n.back.tr,
          onPressed: controller.backToErrorList,
          icon: const Icon(Icons.arrow_back_rounded),
        ),
        Expanded(
          child: Text(title, style: Theme.of(context).textTheme.titleSmall),
        ),
        if (trailing != null) ...[const SizedBox(width: 8), trailing],
      ],
    );
  }

  /// Builds the action that scrolls the error detail view to the latest line.
  Widget _buildScrollToBottomButton() {
    return IconButton(
      tooltip: I18n.homeLogScrollToBottom.tr,
      onPressed: _scrollDetailToBottom,
      icon: const Icon(Icons.vertical_align_bottom_rounded),
    );
  }

  /// Builds the image strip for the selected error detail.
  Widget _buildImages(ScriptErrorLogDetail detail) {
    if (detail.images.isEmpty) {
      return const SizedBox.shrink();
    }
    return SizedBox(
      height: 156,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: detail.images.length,
        separatorBuilder: (context, index) => const SizedBox(width: 8),
        itemBuilder: (context, index) {
          return LogCenterErrorImageCard(
            controller: controller,
            detail: detail,
            image: detail.images[index],
          );
        },
      ),
    );
  }

  /// Builds the unified vertical scroll view for the whole detail page.
  Widget _buildDetailScrollView(
    BuildContext context,
    ScriptErrorLogDetail detail, {
    required String title,
  }) {
    return Scrollbar(
      controller: detailScrollController,
      interactive: true,
      thumbVisibility: true,
      child: CustomScrollView(
        controller: detailScrollController,
        slivers: [
          SliverToBoxAdapter(
            child: _buildHeader(
              context,
              title,
              trailing: _buildScrollToBottomButton(),
            ),
          ),
          const SliverToBoxAdapter(child: SizedBox(height: 8)),
          SliverToBoxAdapter(child: _buildImages(detail)),
          if (detail.images.isNotEmpty)
            const SliverToBoxAdapter(child: SizedBox(height: 6)),
          ..._buildLogSlivers(context),
        ],
      ),
    );
  }

  /// Formats the selected detail timestamp.
  String _formatDetailTime(ScriptErrorLogDetail detail) {
    return controller.formatErrorTimestamp(detail.timestampMs, detail.time);
  }

  /// Resolves one title for the current selected error.
  String _selectedErrorTitle() {
    final selectedId = controller.selectedErrorId.value;
    final index = controller.errorItems.indexWhere((item) {
      return item.id == selectedId;
    });
    if (index < 0) {
      return controller.selectedErrorTitle.value.isEmpty
          ? I18n.homeLogSelectError.tr
          : controller.selectedErrorTitle.value;
    }
    final item = controller.errorItems[index];
    return controller.formatErrorTimestamp(item.timestampMs, item.time);
  }

  /// Scrolls the detail log viewport to the current bottom edge.
  void _scrollDetailToBottom() {
    if (!detailScrollController.hasClients) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!detailScrollController.hasClients) {
        return;
      }
      final target = detailScrollController.position.maxScrollExtent;
      final distance = (detailScrollController.offset - target).abs();
      if (distance > 400) {
        detailScrollController.jumpTo(target);
        return;
      }
      detailScrollController.animateTo(
        target,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
      );
    });
  }
}
