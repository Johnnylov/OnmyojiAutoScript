import 'package:get/get.dart';

import 'package:oasx/modules/home/home_binding.dart';
import 'package:oasx/solana/solana_shell.dart';

class Routes {
  static const initial = '/console';

  static List<GetPage> get routes => build();

  static List<GetPage> build({bool skipStartupActions = false}) => [
    GetPage(
      name: '/console',
      page: () => SolanaShell(skipStartupActions: skipStartupActions),
      binding: HomeBinding(),
    ),
  ];
}
