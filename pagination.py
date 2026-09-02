def is_last_page(next_button_class_attr):
    # DataTables adds a "disabled" class to the next-page button once there's
    # no next page. The button stays present and clickable to Selenium, so a
    # timeout-based check never catches this -- the class is the real signal.
    return "disabled" in next_button_class_attr.split()
